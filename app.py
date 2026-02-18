"""
Article Generator - Flask Web Application
Provides a web interface for the article generator, accessible from any browser.
"""
import os
import json
import uuid
import zipfile
import threading
import queue
from pathlib import Path

from flask import (Flask, render_template, request, jsonify,
                   send_file, Response, session)
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', os.urandom(24))

UPLOAD_FOLDER = Path('uploads')
OUTPUT_FOLDER = Path('output')
UPLOAD_FOLDER.mkdir(exist_ok=True)
OUTPUT_FOLDER.mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {'csv'}

# In-memory job store: job_id -> { queue, status, output_dir, results }
jobs: dict = {}


def allowed_file(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/upload', methods=['POST'])
def upload_csv():
    """Upload a CSV file and return a preview of ACTIVE rows."""
    if 'csv_file' not in request.files:
        return jsonify({'error': 'No file part in request'}), 400

    file = request.files['csv_file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    if not allowed_file(file.filename):
        return jsonify({'error': 'Only .csv files are supported'}), 400

    upload_id = str(uuid.uuid4())
    safe_name = secure_filename(file.filename)
    filepath = UPLOAD_FOLDER / f"{upload_id}_{safe_name}"
    file.save(str(filepath))

    try:
        import pandas as pd
        try:
            df = pd.read_csv(str(filepath), encoding='utf-8')
        except UnicodeDecodeError:
            df = pd.read_csv(str(filepath), encoding='windows-1252')

        total_rows = len(df)

        if 'Status' in df.columns:
            active_df = df[df['Status'].astype(str).str.upper().str.strip() == 'ACTIVE']
        else:
            active_df = df

        active_rows = len(active_df)

        # Build preview (only columns we care about, up to 50 rows)
        preview_cols = [c for c in ['Client Name', 'Title', 'Keywords', 'Website Link', 'Status']
                        if c in active_df.columns]
        preview = active_df[preview_cols].head(50).fillna('').to_dict('records')

        return jsonify({
            'upload_id': upload_id,
            'filepath': str(filepath),
            'total_rows': total_rows,
            'active_rows': active_rows,
            'preview': preview,
            'columns': list(df.columns),
        })

    except Exception as e:
        return jsonify({'error': f'Failed to parse CSV: {e}'}), 500


@app.route('/default-template', methods=['GET'])
def get_default_template():
    """Return the default article prompt template."""
    from generator import DEFAULT_ARTICLE_INSTRUCTIONS
    return jsonify({'template': DEFAULT_ARTICLE_INSTRUCTIONS})


@app.route('/submit-data', methods=['POST'])
def submit_data():
    """Accept inline article rows as JSON, save as CSV, return filepath."""
    data = request.get_json(force=True)
    rows = data.get('rows', [])

    if not rows:
        return jsonify({'error': 'No article rows provided'}), 400

    try:
        import pandas as pd
        # Normalise column names to match what generator.py expects
        df = pd.DataFrame(rows)

        upload_id = str(uuid.uuid4())
        filepath = UPLOAD_FOLDER / f"{upload_id}_inline_data.csv"
        df.to_csv(str(filepath), index=False, encoding='utf-8')

        return jsonify({
            'upload_id': upload_id,
            'filepath': str(filepath),
            'total_rows': len(df),
            'active_rows': len(df),
            'preview': df.head(50).fillna('').to_dict('records'),
            'columns': list(df.columns),
        })
    except Exception as e:
        return jsonify({'error': f'Failed to process data: {e}'}), 500


@app.route('/generate', methods=['POST'])
def start_generation():
    """Start article generation as a background job and return job_id."""
    data = request.get_json(force=True)
    api_key = (data.get('api_key') or '').strip()
    filepath = (data.get('filepath') or '').strip()
    delay = float(data.get('delay', 3.0))
    model = data.get('model', 'claude-sonnet-4-20250514')
    custom_template = (data.get('custom_template') or '').strip() or None

    if not api_key:
        return jsonify({'error': 'Anthropic API key is required'}), 400
    if not filepath or not Path(filepath).exists():
        return jsonify({'error': 'CSV file not found — please upload again'}), 400

    job_id = str(uuid.uuid4())
    job_queue: queue.Queue = queue.Queue()
    output_dir = OUTPUT_FOLDER / job_id
    output_dir.mkdir(parents=True, exist_ok=True)

    jobs[job_id] = {
        'queue': job_queue,
        'status': 'running',
        'output_dir': str(output_dir),
        'results': [],
    }

    thread = threading.Thread(
        target=_run_generation,
        args=(job_id, api_key, filepath, str(output_dir), delay, model, job_queue, custom_template),
        daemon=True,
    )
    thread.start()

    return jsonify({'job_id': job_id})


def _run_generation(job_id, api_key, filepath, output_dir, delay, model, job_queue, custom_template=None):
    """Background thread: runs the generator and pushes progress to the queue."""
    from generator import ArticleGeneratorPureDocx

    def push(message: str):
        job_queue.put({'type': 'log', 'message': message})

    try:
        generator = ArticleGeneratorPureDocx(api_key=api_key, progress_callback=push)
        results = generator.process_csv_file(
            csv_file_path=filepath,
            output_dir=output_dir,
            delay_seconds=delay,
            model=model,
            custom_template=custom_template,
        )
        jobs[job_id]['results'] = results
        jobs[job_id]['status'] = 'complete'

        successful = sum(1 for r in results if r['success'])
        failed = len(results) - successful
        job_queue.put({
            'type': 'complete',
            'successful': successful,
            'failed': failed,
            'results': [
                {'client': r['client'], 'title': r['title'],
                 'success': r['success'], 'error': r.get('error')}
                for r in results
            ],
        })

    except Exception as e:
        jobs[job_id]['status'] = 'error'
        job_queue.put({'type': 'error', 'message': str(e)})


@app.route('/progress/<job_id>')
def progress_stream(job_id):
    """Server-Sent Events endpoint for real-time generation progress."""
    if job_id not in jobs:
        return jsonify({'error': 'Job not found'}), 404

    def event_stream():
        job = jobs[job_id]
        q = job['queue']
        while True:
            try:
                msg = q.get(timeout=25)
                yield f"data: {json.dumps(msg)}\n\n"
                if msg['type'] in ('complete', 'error'):
                    break
            except queue.Empty:
                # Keep-alive ping so the browser doesn't close the connection
                yield f"data: {json.dumps({'type': 'ping'})}\n\n"

    return Response(
        event_stream(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',   # Disables nginx buffering if used
            'Connection': 'keep-alive',
        },
    )


@app.route('/download/<job_id>')
def download_results(job_id):
    """Package all generated files into a ZIP and send to browser."""
    if job_id not in jobs:
        return jsonify({'error': 'Job not found'}), 404

    output_dir = Path(jobs[job_id]['output_dir'])
    zip_path = OUTPUT_FOLDER / f"{job_id}.zip"

    with zipfile.ZipFile(str(zip_path), 'w', zipfile.ZIP_DEFLATED) as zf:
        for file_path in output_dir.rglob('*'):
            if file_path.is_file():
                zf.write(file_path, file_path.relative_to(output_dir))

    return send_file(
        str(zip_path),
        as_attachment=True,
        download_name='generated_articles.zip',
        mimetype='application/zip',
    )


@app.route('/status/<job_id>')
def job_status(job_id):
    """Return the current status of a job (for polling fallback)."""
    if job_id not in jobs:
        return jsonify({'error': 'Job not found'}), 404
    job = jobs[job_id]
    return jsonify({
        'status': job['status'],
        'result_count': len(job['results']),
    })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    print(f"Starting Article Generator web app on http://localhost:{port}")
    app.run(host='0.0.0.0', port=port, debug=debug, threaded=True)
