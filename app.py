import os
import subprocess
import shlex
import logging # Use logging instead of print for server messages
from flask import Flask, render_template, Response, stream_with_context, request, abort

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)

# --- Configuration ---
# Directory containing the executable bash scripts
SCRIPTS_DIR = os.path.abspath("scripts")
# SECURITY: Set to False to disallow users from choosing sudo via the UI,
# even if sudoers is configured.
ALLOW_SUDO_FROM_UI = True # <<< IMPORTANT SECURITY TOGGLE

# --- Helper Functions ---

def list_available_scripts():
    """Scans the SCRIPTS_DIR for executable .sh files."""
    scripts = []
    if not os.path.isdir(SCRIPTS_DIR):
        logging.error(f"Scripts directory not found: {SCRIPTS_DIR}")
        return scripts # Return empty list
    try:
        for filename in os.listdir(SCRIPTS_DIR):
            filepath = os.path.join(SCRIPTS_DIR, filename)
            # Check if it's a file, ends with .sh, and is executable
            if (os.path.isfile(filepath) and
                filename.lower().endswith('.sh') and
                os.access(filepath, os.X_OK)):
                scripts.append(filename) # Inside app.py -> run_script()
        logging.info(f"Found scripts: {scripts}")
    except OSError as e:
        logging.error(f"Error listing scripts in {SCRIPTS_DIR}: {e}")
    scripts.sort() # Sort alphabetically
    return scripts

def stream_script_output(script_path, use_sudo):
    """
    Executes the specified script and yields its output line by line for SSE.
    Handles sudo based on the flag and server configuration.
    """
    command = []

    # --- Sudo Logic ---
    actual_use_sudo = False
    if use_sudo:
        if ALLOW_SUDO_FROM_UI:
            # Check if sudoers is configured for THIS SPECIFIC SCRIPT
            # This requires careful sudoers setup (see notes below)
            # Example sudoers: www-data ALL=(ALL) NOPASSWD: /path/to/webapp/scripts/script2_needs_sudo.sh
            command.append("sudo")
            actual_use_sudo = True
            logging.info(f"Attempting to run script with sudo (UI request): {script_path}")
        else:
            logging.warning(f"Sudo requested by UI for {script_path}, but disallowed by server config (ALLOW_SUDO_FROM_UI=False).")
            # Optionally yield an error message back to the user
            yield "data: __ERROR: Sudo execution via UI is disabled by server configuration.\n\n"
            # Continue without sudo or yield SCRIPT_ERROR? Let's yield error and stop.
            yield "data: __SCRIPT_ERROR__\n\n"
            return # Stop execution

    command.append(script_path)

    try:
        logging.info(f"Executing command: {' '.join(shlex.quote(c) for c in command)}")
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            universal_newlines=True,
            cwd=SCRIPTS_DIR # Optional: Run script from its own directory
        )

        # Stream output
        for line in iter(process.stdout.readline, ''):
            yield f"data: {line.rstrip()}\n\n"

        process.stdout.close()
        return_code = process.wait()

        if return_code == 0:
            logging.info(f"Script '{os.path.basename(script_path)}' finished successfully.")
            yield "data: __SCRIPT_FINISHED__\n\n"
        else:
            logging.warning(f"Script '{os.path.basename(script_path)}' finished with error code: {return_code}")
            # Check if sudo failed specifically (often exit code 1)
            if actual_use_sudo and return_code == 1:
                 yield f"data: [ERROR] Script exited with code {return_code}. If sudo was used, check sudoers configuration and script permissions.\n\n"
            else:
                yield f"data: [ERROR] Script exited with code {return_code}\n\n"
            yield "data: __SCRIPT_ERROR__\n\n"

    except FileNotFoundError:
        # This usually means 'sudo' command wasn't found if actual_use_sudo is True,
        # or the script_path itself is somehow invalid (though we check earlier).
        err_msg = f"Command 'sudo' not found (if sudo was attempted) or script path invalid: {script_path}"
        logging.error(err_msg)
        yield f"data: [ERROR] Server configuration error: {err_msg}\n\n"
        yield "data: __SCRIPT_ERROR__\n\n"
    except PermissionError as e:
        # Might happen if script isn't executable, even if os.access passed earlier (race condition?)
        # Or if sudo fails due to permissions/sudoers issues not caught by exit code 1.
        logging.error(f"Permission error executing script {script_path}: {e}")
        yield f"data: [ERROR] Permission denied executing script. Check file permissions and sudoers configuration if sudo was used.\n\n"
        yield "data: __SCRIPT_ERROR__\n\n"
    except Exception as e:
        logging.exception(f"An unexpected error occurred while running {script_path}: {e}") # Log full traceback
        yield f"data: [ERROR] An unexpected server error occurred: {e}\n\n"
        yield "data: __SCRIPT_ERROR__\n\n"


# --- Routes ---
@app.route('/')
def index():
    """Serves the main HTML page with the list of available scripts."""
    available_scripts = list_available_scripts()
    return render_template('index.html', scripts=available_scripts)

@app.route('/run-script')
def run_script():
    """
    Endpoint to trigger the selected script and stream output via SSE.
    Takes 'script' and 'sudo' as query parameters.
    """
    selected_script_name = request.args.get('script')
    sudo_requested_str = request.args.get('sudo', 'false').lower() # Default to 'false'

    # --- Input Validation ---
    if not selected_script_name:
        logging.warning("Request received without 'script' parameter.")
        # SSE doesn't handle standard HTTP error codes well, send custom error message
        def error_stream():
            yield "data: __ERROR: No script name provided in request.\n\n"
            yield "data: __SCRIPT_ERROR__\n\n"
        return Response(stream_with_context(error_stream()), mimetype='text/event-stream')

    # Security: Prevent path traversal (ensure script is directly in SCRIPTS_DIR)
    if '/' in selected_script_name or '\\' in selected_script_name or '..' in selected_script_name:
         logging.error(f"Invalid script name requested (potential path traversal): {selected_script_name}")
         def error_stream():
            yield f"data: __ERROR: Invalid script name format: {selected_script_name}\n\n"
            yield "data: __SCRIPT_ERROR__\n\n"
         return Response(stream_with_context(error_stream()), mimetype='text/event-stream')

    script_full_path = os.path.join(SCRIPTS_DIR, selected_script_name)

    # Verify the script exists and is executable (double-check)
    if not (os.path.isfile(script_full_path) and os.access(script_full_path, os.X_OK)):
        logging.error(f"Requested script not found or not executable: {script_full_path}")
        def error_stream():
            yield f"data: __ERROR: Script '{selected_script_name}' not found or is not executable on the server.\n\n"
            yield "data: __SCRIPT_ERROR__\n\n"
        return Response(stream_with_context(error_stream()), mimetype='text/event-stream')

    # Check if script is within the allowed directory (redundant but safe)
    if os.path.commonpath([SCRIPTS_DIR]) != os.path.commonpath([SCRIPTS_DIR, script_full_path]):
        logging.error(f"Attempt to access script outside designated directory: {script_full_path}")
        def error_stream():
            yield f"data: __ERROR: Access denied to script: {selected_script_name}\n\n"
            yield "data: __SCRIPT_ERROR__\n\n"
        return Response(stream_with_context(error_stream()), mimetype='text/event-stream')


    use_sudo = sudo_requested_str == 'true'

    logging.info(f"Request received: Run script='{selected_script_name}', Use sudo='{use_sudo}'")

    # Return the streaming response
    return Response(stream_with_context(stream_script_output(script_full_path, use_sudo)), mimetype='text/event-stream')

# --- Main Execution ---
if __name__ == '__main__':
    # Ensure scripts directory exists
    if not os.path.isdir(SCRIPTS_DIR):
        logging.warning(f"Scripts directory '{SCRIPTS_DIR}' not found. Creating it.")
        try:
            os.makedirs(SCRIPTS_DIR)
        except OSError as e:
            logging.error(f"Could not create scripts directory '{SCRIPTS_DIR}': {e}")
            # Decide if you want to exit or continue without scripts
            # exit(1)

    # Check initial scripts (optional, already done in list_available_scripts)
    # list_available_scripts()

    logging.info("Starting Flask server...")
    # Use debug=False in production!
    app.run(host='0.0.0.0', port=5000, debug=True, threaded=True)
