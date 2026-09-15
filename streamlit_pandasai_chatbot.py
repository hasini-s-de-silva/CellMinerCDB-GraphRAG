#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "altair==5.5.0",
#     "annotated-types==0.7.0",
#     "anyio==4.9.0",
#     "astor==0.8.1",
#     "attrs==25.3.0",
#     "blinker==1.9.0",
#     "cachetools==6.1.0",
#     "certifi==2025.6.15",
#     "charset-normalizer==3.4.2",
#     "click==8.2.1",
#     "contourpy==1.3.2",
#     "cycler==0.12.1",
#     "distro==1.9.0",
#     "dnspython==2.7.0",
#     "dotenv==0.9.9",
#     "exceptiongroup==1.3.0",
#     "faker==19.13.0",
#     "fastjsonschema==2.21.1",
#     "flashtext==2.7",
#     "fonttools==4.58.4",
#     "gitdb==4.0.12",
#     "gitpython==3.1.44",
#     "h11==0.16.0",
#     "httpcore==1.0.9",
#     "httpx==0.28.1",
#     "idna==3.10",
#     "jinja2==3.1.6",
#     "jiter==0.10.0",
#     "joblib==1.5.1",
#     "jsonschema==4.24.0",
#     "jsonschema-specifications==2025.4.1",
#     "kiwisolver==1.4.8",
#     "markupsafe==3.0.2",
#     "matplotlib==3.7.5",
#     "narwhals==1.44.0",
#     "numpy==1.26.4",
#     "openai==1.82.0",
#     "packaging==25.0",
#     "pandas==2.3.0",
#     "pandasai==3.0.0b17",
#     "pandasai-openai==0.1.5",
#     "pandasai-sql==0.1.7",
#     "pillow==10.4.0",
#     "protobuf==6.31.1",
#     "psycopg2-binary==2.9.10",
#     "pyarrow==14.0.2",
#     "pydantic==2.11.7",
#     "pydantic-core==2.33.2",
#     "pydeck==0.9.1",
#     "pymongo==4.13.0",
#     "pyparsing==3.2.3",
#     "python-dateutil==2.9.0.post0",
#     "python-dotenv==1.1.1",
#     "pytz==2025.2",
#     "pyyaml==6.0.2",
#     "referencing==0.36.2",
#     "requests==2.32.4",
#     "rpds-py==0.25.1",
#     "scikit-learn==1.7.0",
#     "scipy==1.10.1",
#     "seaborn==0.12.2",
#     "six==1.17.0",
#     "smmap==5.0.2",
#     "sniffio==1.3.1",
#     "sqlalchemy==2.0.41",
#     "sqlglot==25.34.1",
#     "sqlglotrs==0.3.0",
#     "streamlit==1.46.1",
#     "tenacity==9.1.2",
#     "threadpoolctl==3.6.0",
#     "toml==0.10.2",
#     "tomli==2.2.1",
#     "tornado==6.5.1",
#     "tqdm==4.67.1",
#     "trove-classifiers==2025.5.9.12",
#     "typing-extensions==4.14.0",
#     "typing-inspection==0.4.1",
#     "tzdata==2025.2",
#     "urllib3==2.5.0",
#     "validate-pyproject==0.24.1",
#     "watchdog==6.0.0",
#     "duckdb==1.3.1",
#     "psutil==7.0.0"
# ]
# ///

import os
import time
import getpass
import textwrap
import shutil
import streamlit as st
import matplotlib.pyplot as plt
from dotenv import load_dotenv
import os
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))
from pandasai_openai import AzureOpenAI
from pandasai import Agent
from pandasai.config import Config
from PIL import Image
from pandasai_helper import create_postgres_dataset, harmonize_cell_line_names, build_cell_line_keyword_processor, build_drug_keyword_processor, harmonize_drug_names
import logging
import re
import pandas as pd
import numpy as np
import seaborn as sns
from typing import Optional
import base64
import streamlit.components.v1 as components
import io
import psycopg2

# Load environment variables
load_dotenv()

def _purge_cellminer_datasets():
    """Recursively delete everything inside ./datasets/cellminer/ (if it exists).

    This prevents stale or corrupted artifacts from previous sessions from
    interfering with the current run. Any deletion errors are logged but do not
    stop the app from launching."""

    import shutil, logging as _logging

    base_dir = os.path.dirname(os.path.abspath(__file__))
    target_dir = os.path.join(base_dir, "datasets", "cellminer")

    if not os.path.exists(target_dir):
        return  # Nothing to purge

    for entry in os.listdir(target_dir):
        entry_path = os.path.join(target_dir, entry)
        try:
            if os.path.isfile(entry_path) or os.path.islink(entry_path):
                os.unlink(entry_path)
            else:
                shutil.rmtree(entry_path)
        except Exception as purge_err:
            _logging.warning("Could not delete %s: %s", entry_path, purge_err)


# Execute purge once at import-time
_purge_cellminer_datasets()

# Custom logging handler to capture generated code from PandasAI
class PandasAICodeCaptureHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.captured_code = None
        self.captured_executed_code = None
        self.sql_output_folder = "sql_outputs"
        self._ensure_sql_folder()
        
    def _ensure_sql_folder(self):
        """Ensure the SQL output folder exists"""
        if not os.path.exists(self.sql_output_folder):
            os.makedirs(self.sql_output_folder)
            logging.info(f"Created SQL output folder: {self.sql_output_folder}")
    
    def _save_sql_to_file(self, sql_query: str, user_query: str = None):
        """Save SQL query to a timestamped file in the sql_outputs subfolder"""
        if not sql_query:
            return
        
        try:
            # Create timestamp for filename
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            
            # Create a safe filename from user query (if available)
            safe_filename = "query"
            if user_query:
                # Clean the user query to create a safe filename
                safe_filename = re.sub(r'[^\w\s-]', '', user_query)[:50].strip()
                safe_filename = re.sub(r'[-\s]+', '_', safe_filename)
                if not safe_filename:
                    safe_filename = "query"
            
            # Create the full filename
            filename = f"{timestamp}_{safe_filename}.sql"
            filepath = os.path.join(self.sql_output_folder, filename)
            
            # Write SQL to file
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(f"-- Generated SQL from PandasAI\n")
                f.write(f"-- Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                if user_query:
                    f.write(f"-- User Query: {user_query}\n")
                f.write(f"-- \n")
                f.write(sql_query)
                f.write("\n")
            
            logging.info(f"SQL statement saved to: {filepath}")
            
        except Exception as e:
            logging.error(f"Failed to save SQL to file: {e}")
    
    def emit(self, record):
        message = record.getMessage()
        # Capture the generated code from PandasAI logs
        if "Code Generated:" in message:
            # Extract code after "Code Generated:"
            code_match = re.search(r'Code Generated:\s*\n(.*)', message, re.DOTALL)
            if code_match:
                self.captured_code = code_match.group(1).strip()
                # Try to extract and save SQL if present
                extracted_sql = _extract_sql_from_captured_code(self.captured_code)
                if extracted_sql:
                    self._save_sql_to_file(extracted_sql)
        elif "Executing code:" in message:
            # Extract the actual executed code
            code_match = re.search(r'Executing code:\s*(.*)', message, re.DOTALL)
            if code_match:
                self.captured_executed_code = code_match.group(1).strip()
                # Try to extract and save SQL if present
                extracted_sql = _extract_sql_from_captured_code(self.captured_executed_code)
                if extracted_sql:
                    self._save_sql_to_file(extracted_sql)
    
    def get_captured_code(self):
        """Get the most recently captured code, preferring executed code over generated code"""
        return self.captured_executed_code or self.captured_code
    
    def clear_captured_code(self):
        """Clear the captured code"""
        self.captured_code = None
        self.captured_executed_code = None
    
    def save_sql_with_context(self, sql_query: str, user_query: str = None):
        """Save SQL query with user context for better file naming"""
        self._save_sql_to_file(sql_query, user_query)

# Initialize the global code capture handler
if 'code_capture_handler' not in st.session_state:
    st.session_state.code_capture_handler = PandasAICodeCaptureHandler()
    
    # Add the handler to the pandasai logger **and its root logger** for broader coverage
    pandasai_helper_logger = logging.getLogger('pandasai.helpers.logger')
    pandasai_root_logger   = logging.getLogger('pandasai')

    for _lg in (pandasai_helper_logger, pandasai_root_logger):
        _lg.addHandler(st.session_state.code_capture_handler)
        _lg.setLevel(logging.INFO)

# Set matplotlib font
plt.rcParams["font.family"] = "Arial"
# 🆕 Ensure every figure saved by PandasAI (or any other library) produces an
# accompanying SVG file for high-quality vector graphics.  We monkey-patch
# matplotlib.pyplot.savefig once at import-time so any downstream call that
# writes, e.g. "my_plot.png" automatically emits "my_plot.svg" alongside it.
import matplotlib.pyplot as _plt

if not hasattr(_plt, "_orig_savefig"):
    _plt._orig_savefig = _plt.savefig  # type: ignore[attr-defined]

    def _svg_only_savefig(fname, *args, **kwargs):  # noqa: D401 - thin wrapper
        """Intercept save requests and always write a vector `.svg` file only."""
        import os.path as _os_path

        if isinstance(fname, str):
            base, ext = _os_path.splitext(fname)

            # Determine target SVG path
            svg_path = fname if ext.lower() == ".svg" else base + ".svg"

            # Always save in SVG format, ignoring any requested bitmap format
            if "format" in kwargs:
                kwargs.pop("format")
            # Ensure tight bounding box for no extra whitespace
            kwargs.setdefault('bbox_inches', 'tight')
            _plt._orig_savefig(svg_path, format="svg", *args, **kwargs)  # type: ignore[attr-defined]
        else:
            # Fallback - if a non-string path-like object was supplied
            if "format" in kwargs:
                kwargs.pop("format")
            kwargs.setdefault('bbox_inches', 'tight')
            _plt._orig_savefig(fname, format="svg", *args, **kwargs)  # type: ignore[attr-defined]

    # Override the public savefig reference
    _plt.savefig = _svg_only_savefig  # type: ignore[assignment]

# Page configuration
st.set_page_config(
    page_title="CellMinerCDB Data Analysis Chatbot",
    page_icon="🧬",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={
        'Get Help': None,
        'Report a bug': None,
        'About': None
    }
)

# Custom CSS for styling
st.markdown("""
<style>
    html, body {
        height: 100%;
        margin: 0;
        padding: 0;
    }
    .main {
        background-color: #f5f7f9;
        min-height: 100vh;
        width: 100%;
        padding: 0;
        margin: 0;
    }
    .stApp {
        width: 100%;
        max-width: 100%;
        margin: 0;
        padding: 0;
        min-height: 100vh;
        position: relative;
    }
    .block-container {
        width: 100%;
        max-width: 100%;
        padding: 1rem;
        margin: 0;
    }
    h1, h2, h3 {
        color: #2c3e50;
    }
    .stChat {
        border-radius: 10px;
    }
    .stChatMessage {
        padding: 10px;
        border-radius: 15px;
        margin-bottom: 10px;
    }
    /* Remove default Streamlit padding */
    .css-1d391kg {
        padding: 0;
    }
    .css-18e3th9 {
        padding: 0;
    }
    /* Make sidebar responsive */
    .css-1lcbmhc {
        width: 300px;
    }
    /* Ensure content area uses full width */
    .css-1v0mbdj {
        width: 100%;
    }
</style>
""", unsafe_allow_html=True)

# Initialize session state
if 'messages' not in st.session_state:
    st.session_state.messages = []
    
if 'agent' not in st.session_state:
    st.session_state.agent = None
    
if 'cellminer_dataset' not in st.session_state:
    st.session_state.cellminer_dataset = None

if 'llm_instance' not in st.session_state:
    st.session_state.llm_instance = None

if 'query_history' not in st.session_state:
    st.session_state.query_history = []

# ------------------------------
# ⚡️ PostgreSQL constants & helpers
# ------------------------------

DATASET_PATH = "postgres/cellminer-data-new"  # PandasAI dataset path (org/dataset)
TABLE_NAME   = "cellminer_data_v2"  # Remote PostgreSQL table name

# Add this configuration validation function after the imports
def validate_azure_openai_config():
    """Validate Azure OpenAI configuration and provide helpful error messages."""
    config_issues = []
    
    # Check required environment variables
    required_vars = [
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_API_KEY", 
        "AZURE_OPENAI_API_VERSION",
        "AZURE_OPENAI_DEPLOYMENT_NAME"
    ]
    
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    if missing_vars:
        config_issues.append(f"Missing environment variables: {', '.join(missing_vars)}")
    
    # Validate endpoint format
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    if endpoint and not endpoint.startswith("https://"):
        config_issues.append("AZURE_OPENAI_ENDPOINT must start with 'https://'")
    if endpoint and not endpoint.endswith("/"):
        config_issues.append("AZURE_OPENAI_ENDPOINT should end with '/'")
    if endpoint and "openai.azure.com" not in endpoint:
        config_issues.append("AZURE_OPENAI_ENDPOINT should contain 'openai.azure.com'")
    
    # Validate API version format
    api_version = os.getenv("AZURE_OPENAI_API_VERSION")
    if api_version and not api_version.startswith("20"):
        config_issues.append("AZURE_OPENAI_API_VERSION should be in format 'YYYY-MM-DD' or 'YYYY-MM-DD-preview'")
    
    return config_issues

def test_azure_openai_connection():
    """Test the Azure OpenAI connection and deployment."""
    try:
        from openai import AzureOpenAI
        
        # Create a test client with proper configuration
        test_client = AzureOpenAI(
            azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            api_version=os.getenv("AZURE_OPENAI_API_VERSION")
        )
        
        # Test with a simple completion
        response = test_client.chat.completions.create(
            model=os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME"),
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=10
        )
        
        return True, "Azure OpenAI connection successful"
        
    except Exception as e:
        error_msg = str(e)
        if "404" in error_msg:
            return False, "404 Error: Check deployment name and endpoint URL"
        elif "401" in error_msg:
            return False, "401 Error: Check API key"
        elif "403" in error_msg:
            return False, "403 Error: Check permissions and region"
        else:
            return False, f"Connection error: {error_msg}"

def test_api_versions_for_fallback():
    """Test different API versions to find a working one for automatic fallback."""
    api_versions_to_test = [
        "2024-05-01-preview",  # Known working version from logs
        "2024-02-15-preview",  # Stable version
        "2023-12-01-preview",  # Older stable version
        "2023-05-15",          # Non-preview stable version
    ]
    
    try:
        from openai import AzureOpenAI
        
        endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        api_key = os.getenv("AZURE_OPENAI_API_KEY")
        deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")
        
        if not all([endpoint, api_key, deployment]):
            return None
        
        for version in api_versions_to_test:
            try:
                test_client = AzureOpenAI(
                    azure_endpoint=endpoint,
                    api_key=api_key,
                    api_version=version
                )
                
                # Simple test request
                response = test_client.chat.completions.create(
                    model=deployment,
                    messages=[{"role": "user", "content": "Test"}],
                    max_tokens=5
                )
                
                return version  # Return the first working version
                
            except Exception:
                continue
                
        return None
        
    except ImportError:
        return None

# Helper function to make SVG responsive for Streamlit display
def _make_svg_responsive(svg_content: str) -> str:
    """
    Modify SVG content to be responsive and fit properly in Streamlit containers.
    
    Args:
        svg_content: Original SVG content as string
        
    Returns:
        Modified SVG content with responsive attributes
    """
    import re
    
    # Extract viewBox if it exists to preserve aspect ratio
    viewbox_match = re.search(r'viewBox="([^"]*)"', svg_content)
    viewbox = viewbox_match.group(1) if viewbox_match else "0 0 800 600"
    
    # Remove fixed width and height attributes from the SVG tag
    svg_content = re.sub(r'width="[^"]*"', '', svg_content)
    svg_content = re.sub(r'height="[^"]*"', '', svg_content)
    
    # Ensure viewBox is present for proper scaling
    if not viewbox_match:
        svg_content = re.sub(r'<svg([^>]*)>', fr'<svg\1 viewBox="{viewbox}">', svg_content)
    
    # Add responsive attributes to the SVG tag
    svg_content = re.sub(
        r'<svg([^>]*)>',
        r'<svg\1 width="100%" height="auto" style="max-width: 100%; max-height: 500px;" preserveAspectRatio="xMidYMid meet">',
        svg_content
    )
    
    # Wrap in a responsive container div with better styling
    responsive_wrapper = f"""
    <div style="
        width: 100%; 
        max-width: 100%; 
        display: flex; 
        justify-content: center; 
        align-items: center; 
        background-color: white;
        border-radius: 8px;
        padding: 10px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        overflow: visible;
        ">
        {svg_content}
    </div>
    """
    
    return responsive_wrapper

# Helper function to get table row count estimate from PostgreSQL
def get_table_row_estimate(host: str, port: int, user: str, password: str, database: str, table_name: str) -> int | None:
    """Get table row count estimate using PostgreSQL's pg_class.reltuples."""
    import psycopg2
    
    try:
        conn = psycopg2.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            dbname=database,
            connect_timeout=10
        )
        
        with conn.cursor() as cur:
            # Use pg_class.reltuples for fast row estimate
            cur.execute(
                "SELECT reltuples::BIGINT AS estimate FROM pg_class WHERE oid = %s::regclass;",
                (table_name,)
            )
            result = cur.fetchone()
            return result[0] if result else None
            
    except Exception as e:
        print(f"Error getting row estimate: {e}")
        return None
    finally:
        if 'conn' in locals():
            conn.close()

# Helper function to clean up captured code for download
def clean_captured_code_for_download(raw_code: str, output_filename: str) -> str:
    """
    Clean up the raw captured code from PandasAI for better user experience.
    
    Args:
        raw_code: The raw code captured from PandasAI logs
        output_filename: The target filename for the plot
        
    Returns:
        Cleaned code string ready for download with PEP 723 shebang and CSV data loading
    """
    if not raw_code:
        return None
    
    # Clean up the code and remove any leading/trailing whitespace
    cleaned_code = raw_code.strip()

    # Try to extract the SQL query from the captured code; fall back to session state
    extracted_sql = None
    try:
        extracted_sql = _extract_sql_from_captured_code(raw_code)
    except Exception:
        extracted_sql = None
    if not extracted_sql:
        try:
            import streamlit as _st
            extracted_sql = _st.session_state.get('current_query_sql')
        except Exception:
            extracted_sql = None

    # Remove any in-body redefinition of sql_query to avoid conflicts with injected header variable
    cleaned_code = re.sub(r'^\s*sql_query\s*=\s*.*$', '', cleaned_code, flags=re.MULTILINE)

    # Ensure any execute_sql_query(...) calls include the download_csv flag (DOWNLOAD_CSV)
    # Specific common case
    cleaned_code = re.sub(
        r'execute_sql_query\(\s*sql_query\s*\)',
        'execute_sql_query(sql_query, download_csv=DOWNLOAD_CSV)',
        cleaned_code,
    )
    # Generic case
    cleaned_code = re.sub(
        r'execute_sql_query\(([^\)]*)\)',
        lambda m: (
            m.group(0)
            if 'download_csv' in m.group(1)
            else f"execute_sql_query({m.group(1)}, download_csv=DOWNLOAD_CSV)"
        ),
        cleaned_code,
    )
    
    # Remove any stray empty strings or incomplete lines
    cleaned_code = re.sub(r'^\s*""\s*$', '', cleaned_code, flags=re.MULTILINE)
    cleaned_code = re.sub(r'^\s*\'\'\s*$', '', cleaned_code, flags=re.MULTILINE)
    
    # Replace data.empty checks with len(data) == 0 for VirtualDataFrame compatibility
    cleaned_code = re.sub(r'data\.empty', 'len(data) == 0', cleaned_code)
    cleaned_code = re.sub(r'if\s+data\.empty\s*:', 'if len(data) == 0:', cleaned_code)
    
    # Update plot save paths to use the provided filename
    cleaned_code = re.sub(r"plot_path\s*=\s*'[^']*'", f"output_path = '{output_filename}'", cleaned_code)
    cleaned_code = re.sub(r"png_path\s*=\s*'[^']*'", f"output_path = '{output_filename}'", cleaned_code)
    
    # Update savefig calls to use output_path and SVG format
    cleaned_code = re.sub(r"plt\.savefig\(plot_path\)", "plt.savefig(output_path, format='svg', bbox_inches='tight')", cleaned_code)
    cleaned_code = re.sub(r"plt\.savefig\(png_path\)", "plt.savefig(output_path, format='svg', bbox_inches='tight')", cleaned_code)
    cleaned_code = re.sub(r"plt\.savefig\('[^']*'\)", f"plt.savefig(output_path, format='svg', bbox_inches='tight')", cleaned_code)
    
    # Fix any result assignments that reference undefined plot_path or png_path
    cleaned_code = re.sub(r"result\s*=\s*\{\s*'type':\s*'plot',\s*'value':\s*plot_path\s*\}", "result = {'type': 'plot', 'value': output_path}", cleaned_code)
    cleaned_code = re.sub(r"result\s*=\s*\{\s*'type':\s*'plot',\s*'value':\s*png_path\s*\}", "result = {'type': 'plot', 'value': output_path}", cleaned_code)
    
    # Remove plt.show() to prevent display and just save the plot
    cleaned_code = re.sub(r'plt\.show\(\)', '', cleaned_code)
    
    # Remove any plt.close() calls to let the user handle cleanup
    if 'plt.close()' in cleaned_code:
        cleaned_code = re.sub(r'plt\.close\(\)', '', cleaned_code)
    
    # Clean up any extra whitespace or empty lines
    cleaned_code = re.sub(r'\n\s*\n\s*\n', '\n\n', cleaned_code)
    cleaned_code = cleaned_code.strip()
    
    # PEP 723 shebang with essential dependencies and SQL/CSV helpers
    pep_723_header = '''#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "pandas>=2.0.0",
#     "matplotlib>=3.7.0",
#     "seaborn>=0.12.0",
#     "psycopg2-binary>=2.9.9",
# ]
# ///

"""
Code generated by PandasAI for plot creation
This script recreates the exact visualization using the exported data.

Usage:
    1. Ensure you have both files in the same directory:
       - recreate_plot.py (this file)
       - data.csv (the exported data)
       
    2. Run: uv run recreate_plot.py
    
    Or install dependencies manually and run with python:
        pip install pandas matplotlib seaborn
        python recreate_plot.py

Note: This script can either load data from 'data.csv' (default) or, if you set
DOWNLOAD_CSV=True, it will fetch fresh data from PostgreSQL using your DBEAVER_*
environment variables and save 'data.csv' as an intermediate artefact.
"""

import os
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

DOWNLOAD_CSV = False  # Set to True to fetch data via DBEAVER_* env and write data.csv

# Embed the exact SQL used for this request (if detected)
sql_query = __INJECTED_SQL__

def _parse_dbeaver_url(url: str) -> dict:
    from urllib.parse import urlparse
    if not url:
        return {}
    if not url.startswith('postgresql://'):
        url = f'postgresql://{url}'
    parsed = urlparse(url)
    return {"host": parsed.hostname, "port": parsed.port}

def execute_sql_query(query: str, download_csv: bool = False) -> pd.DataFrame:
    """Execute SQL using only DBEAVER_* env vars when download_csv=True; else load local CSV.

    - When download_csv is False (default), returns DataFrame loaded from 'data.csv'.
    - When True, connects to PostgreSQL using DBEAVER_URL/DBEAVER_DATABASE/DBEAVER_USERNAME/DBEAVER_PASSWORD,
      executes the SQL, saves results to 'data.csv', and returns the DataFrame.
    """
    if not download_csv:
        if not os.path.exists('data.csv'):
            raise FileNotFoundError("data.csv not found. Set DOWNLOAD_CSV=True to fetch data from the database.")
        return pd.read_csv('data.csv')

    import psycopg2
    db_url = os.getenv("DBEAVER_URL")
    database = os.getenv("DBEAVER_DATABASE")
    user = os.getenv("DBEAVER_USERNAME")
    password = os.getenv("DBEAVER_PASSWORD")
    params = _parse_dbeaver_url(db_url) if db_url else {}
    host, port = params.get("host"), params.get("port")

    missing = []
    if not db_url: missing.append("DBEAVER_URL")
    if not database: missing.append("DBEAVER_DATABASE")
    if not user: missing.append("DBEAVER_USERNAME")
    if not password: missing.append("DBEAVER_PASSWORD")
    if not host: missing.append("host (from DBEAVER_URL)")
    if not port: missing.append("port (from DBEAVER_URL)")
    if missing:
        raise RuntimeError(f"Database connection info missing: {', '.join(missing)}")

    with psycopg2.connect(host=host, port=port, user=user, password=password, dbname=database) as _conn:
        df = pd.read_sql_query(query, _conn)
    df.to_csv('data.csv', index=False)
    return df

# Generated analysis code starts here
'''
    
    # Footer with execution instructions
    footer = '''

# Save the plot (no display)
plt.tight_layout()
print(f'Plot saved as: {output_path}')
print("Analysis complete! The plot has been saved using the exported data.")
print("Note: The plot is saved but not displayed. Open the SVG file to view it.")
'''
    
    injected_sql_text = repr(extracted_sql) if extracted_sql else "None"
    return pep_723_header.replace("__INJECTED_SQL__", injected_sql_text) + cleaned_code + footer

# ➕ Function to detect if a query is requesting a plot/visualization
def is_plot_request(query: str) -> bool:
    """Detect if a user query is requesting a visualization/plot."""
    plot_keywords = [
        'plot', 'chart', 'graph', 'visualize', 'visualization', 'histogram', 'scatter', 
        'boxplot', 'heatmap', 'barplot', 'line plot', 'distribution', 'correlation plot',
        'show', 'display', 'draw', 'create a plot', 'create a chart', 'create a graph',
        'plot the', 'chart the', 'graph the', 'visualize the'
    ]
    
    query_lower = query.lower()
    is_plot = any(keyword in query_lower for keyword in plot_keywords)
    
    if is_plot:
        matched_keywords = [keyword for keyword in plot_keywords if keyword in query_lower]
        logging.info(f"🔍 Plot request detected. Matched keywords: {matched_keywords}")
    
    return is_plot

# ➕ Function to handle CSV-first plotting workflow
def handle_csv_first_plotting(user_query: str, agent) -> tuple[bool, any]:
    """
    Handle plotting requests using CSV-first approach.
    Returns (success, response) tuple.
    """
    try:
        # Step 1: Intercept and modify the prompt to ensure we get data first
        enhanced_prompt = build_enhanced_prompt(user_query)
        
        # Modify the enhanced prompt to be more explicit about data extraction
        data_first_prompt = f"""
        {enhanced_prompt}
        
        IMPORTANT: Before creating any visualization, first execute the SQL query and return the raw data.
        Then use that data to create the plot.
        
        Follow this exact sequence:
        1. Execute the SQL query to get the data
        2. Store the data in a variable called 'data'  
        3. Create the visualization using matplotlib/seaborn
        4. Save the plot to the exports/charts/ directory
        
        Make sure to return the data as a DataFrame first, then create the plot.
        """
        
        # Clear previous code capture to get fresh code
        st.session_state.code_capture_handler.clear_captured_code()
        
        logging.info("Step 1: Executing enhanced prompt with data-first approach...")
        
        # Execute the query normally but capture the intermediate data
        response = agent.chat(data_first_prompt)
        
        # Step 2: Try to extract CSV data from the captured code execution
        captured_code = st.session_state.code_capture_handler.get_captured_code()
        csv_data = None
        
        if captured_code:
            try:
                # Try to regenerate the data from the captured code
                regenerated_df = _regenerate_dataframe_from_code(captured_code)
                if regenerated_df is not None:
                    csv_buffer = io.StringIO()
                    regenerated_df.to_csv(csv_buffer, index=False)
                    csv_data = csv_buffer.getvalue()
                    # Store the CSV data for later retrieval
                    st.session_state.csv_first_data = csv_data
                    logging.info(f"Successfully extracted CSV data: {len(regenerated_df)} rows, {len(regenerated_df.columns)} columns")
                else:
                    logging.warning("Could not regenerate DataFrame from captured code")
            except Exception as regen_err:
                logging.error(f"Failed to regenerate DataFrame: {regen_err}")
        
        # Step 3: If regeneration failed, try to extract SQL and execute it directly
        if csv_data is None and captured_code:
            try:
                extracted_sql = _extract_sql_from_captured_code(captured_code)
                if extracted_sql:
                    logging.info(f"Attempting direct SQL execution: {extracted_sql[:100]}...")
                    
                    # Save the extracted SQL to file with user context
                    st.session_state.code_capture_handler.save_sql_with_context(extracted_sql, user_query)
                    
                    dataset = st.session_state.get("cellminer_dataset")
                    if dataset and hasattr(dataset, "_loader") and hasattr(dataset._loader, "execute_sql_query"):
                        sql_result = dataset._loader.execute_sql_query(extracted_sql)
                        converted_df = _safe_dataframe_conversion(sql_result, "Direct SQL execution for CSV")
                        if converted_df is not None:
                            csv_buffer = io.StringIO()
                            converted_df.to_csv(csv_buffer, index=False)
                            csv_data = csv_buffer.getvalue()
                            st.session_state.csv_first_data = csv_data
                            logging.info(f"Direct SQL execution successful: {len(converted_df)} rows")
            except Exception as direct_sql_err:
                logging.error(f"Direct SQL execution failed: {direct_sql_err}")
        
        # Step 4: Return the response (plot should be created)
        return True, response
        
    except Exception as e:
        logging.error(f"CSV-first plotting failed: {e}")
        return False, f"CSV-first plotting failed: {e}"

# ➕ Helper: regenerate the intermediate DataFrame from captured PandasAI code

def _extract_sql_from_captured_code(captured_code: str) -> str | None:
    """Extract SQL query from captured PandasAI code for fallback CSV generation."""
    if not captured_code:
        return None
    
    # Look for SQL query patterns in the code
    import re
    
    # Clean up the code first - remove any Python comments
    cleaned_code = re.sub(r'#.*$', '', captured_code, flags=re.MULTILINE)
    
    # Pattern 1: sql_query = "..." or sql_query = '...' (multi-line)
    sql_pattern = r'sql_query\s*=\s*["\']([^"\']*(?:["\'][^"\']*)*)["\']'
    match = re.search(sql_pattern, cleaned_code, re.DOTALL)
    if match:
        sql = match.group(1).strip()
        # Clean up any escaped quotes
        sql = sql.replace('\\"', '"').replace("\\'", "'")
        return sql
    
    # Pattern 2: execute_sql_query("...") or execute_sql_query('...')
    exec_pattern = r'execute_sql_query\s*\(\s*["\']([^"\']*(?:["\'][^"\']*)*)["\']'
    match = re.search(exec_pattern, cleaned_code, re.DOTALL)
    if match:
        sql = match.group(1).strip()
        sql = sql.replace('\\"', '"').replace("\\'", "'")
        return sql
    
    # Pattern 3: Look for direct SQL statements (WITH clauses or SELECT statements)
    # This pattern looks for SQL that spans multiple lines
    select_patterns = [
        # Pattern for WITH ... SELECT
        r'(WITH\s+[^;]+SELECT\s+.*?FROM\s+.*?(?:WHERE\s+.*?)?(?:GROUP\s+BY\s+.*?)?(?:ORDER\s+BY\s+.*?)?(?:LIMIT\s+.*?)?;?)',
        # Pattern for standalone SELECT
        r'(SELECT\s+.*?FROM\s+.*?(?:WHERE\s+.*?)?(?:GROUP\s+BY\s+.*?)?(?:ORDER\s+BY\s+.*?)?(?:LIMIT\s+.*?)?;?)',
        # Pattern for more complex queries
        r'(["\'])((?:WITH\s+.*?\s+)?SELECT\s+.*?FROM\s+.*?)\1'
    ]
    
    for pattern in select_patterns:
        match = re.search(pattern, cleaned_code, re.DOTALL | re.IGNORECASE)
        if match:
            sql = match.group(1) if len(match.groups()) == 1 else match.group(2)
            sql = sql.strip()
            # Clean up any artifacts
            sql = sql.replace('\\"', '"').replace("\\'", "'")
            # Remove trailing semicolon if present
            sql = sql.rstrip(';')
            if sql and ('SELECT' in sql.upper() or 'WITH' in sql.upper()):
                return sql
    
    # Pattern 4: Look for SQL inside triple quotes
    triple_quote_pattern = r'["\'\`]{3}(.*?SELECT.*?)["\'\`]{3}'
    match = re.search(triple_quote_pattern, cleaned_code, re.DOTALL | re.IGNORECASE)
    if match:
        sql = match.group(1).strip()
        return sql
    
    return None

def save_sql_statement(sql_query: str, user_query: str = None, context: str = "manual"):
    """Utility function to manually save SQL statements to file.
    
    Args:
        sql_query: The SQL query to save
        user_query: The user's original query for context
        context: Additional context about when/why the SQL was saved
    """
    if not sql_query:
        return
    
    try:
        # Ensure the SQL output folder exists
        sql_folder = "sql_outputs"
        if not os.path.exists(sql_folder):
            os.makedirs(sql_folder)
        
        # Create timestamp for filename
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        
        # Create a safe filename
        safe_filename = "manual_sql"
        if user_query:
            # Clean the user query to create a safe filename
            safe_filename = re.sub(r'[^\w\s-]', '', user_query)[:50].strip()
            safe_filename = re.sub(r'[-\s]+', '_', safe_filename)
            if not safe_filename:
                safe_filename = "manual_sql"
        
        # Create the full filename
        filename = f"{timestamp}_{safe_filename}_{context}.sql"
        filepath = os.path.join(sql_folder, filename)
        
        # Write SQL to file
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(f"-- Manually Saved SQL Statement\n")
            f.write(f"-- Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"-- Context: {context}\n")
            if user_query:
                f.write(f"-- User Query: {user_query}\n")
            f.write(f"-- \n")
            f.write(sql_query)
            f.write("\n")
        
        logging.info(f"SQL statement manually saved to: {filepath}")
        return filepath
        
    except Exception as e:
        logging.error(f"Failed to manually save SQL to file: {e}")
        return None

def _safe_dataframe_conversion(df_candidate, operation_name: str = "operation"):
    """Safely convert any DataFrame-like object to pandas DataFrame with proper validation."""
    import pandas as pd
    
    if df_candidate is None:
        logging.warning(f"{operation_name}: Received None instead of DataFrame")
        return None
    
    try:
        # Try to convert to pandas DataFrame if it's not already
        if not isinstance(df_candidate, pd.DataFrame):
            logging.info(f"{operation_name}: Converting {type(df_candidate)} to pandas DataFrame")
            
            # Special handling for VirtualDataFrame objects
            df_type_name = type(df_candidate).__name__
            if "VirtualDataFrame" in df_type_name or "Virtual" in df_type_name:
                logging.info(f"{operation_name}: Detected VirtualDataFrame, attempting conversion")
                # Try various methods for VirtualDataFrame conversion
                try:
                    if hasattr(df_candidate, '_df') and df_candidate._df is not None:
                        df_candidate = df_candidate._df
                        logging.info(f"{operation_name}: Converted using ._df attribute")
                    elif hasattr(df_candidate, '_pandas_df'):
                        df_candidate = df_candidate._pandas_df
                        logging.info(f"{operation_name}: Converted using ._pandas_df attribute")
                    elif hasattr(df_candidate, 'to_pandas'):
                        df_candidate = df_candidate.to_pandas()
                        logging.info(f"{operation_name}: Converted using .to_pandas()")
                    else:
                        # Try to force evaluation by accessing data
                        try:
                            # This might force the VirtualDataFrame to materialize
                            df_candidate = pd.DataFrame(df_candidate)
                            logging.info(f"{operation_name}: Forced conversion to pandas DataFrame")
                        except Exception as force_err:
                            logging.warning(f"{operation_name}: Could not force VirtualDataFrame conversion: {force_err}")
                except Exception as vdf_err:
                    logging.warning(f"{operation_name}: VirtualDataFrame conversion failed: {vdf_err}")
                    # Continue to try other methods below
                
            # Check for common DataFrame conversion methods
            if hasattr(df_candidate, 'to_pandas'):
                df_candidate = df_candidate.to_pandas()
                logging.info(f"{operation_name}: Converted using .to_pandas()")
            elif hasattr(df_candidate, 'toPandas'):
                df_candidate = df_candidate.toPandas()
                logging.info(f"{operation_name}: Converted using .toPandas()")
            elif hasattr(df_candidate, 'collect'):
                # Spark DataFrame
                df_candidate = df_candidate.toPandas()
                logging.info(f"{operation_name}: Converted Spark DataFrame using .toPandas()")
            elif hasattr(df_candidate, '__array__') or hasattr(df_candidate, '__iter__'):
                # Try to create DataFrame from array-like object
                df_candidate = pd.DataFrame(df_candidate)
                logging.info(f"{operation_name}: Created DataFrame from array-like object")
            else:
                logging.warning(f"{operation_name}: Unknown DataFrame type {type(df_candidate)}, attempting direct conversion")
                df_candidate = pd.DataFrame(df_candidate)
        
        # Validate that it's a proper DataFrame with data
        if isinstance(df_candidate, pd.DataFrame):
            # Use multiple validation approaches for robustness
            has_data = False
            validation_method = None
            
            try:
                has_data = len(df_candidate) > 0 and len(df_candidate.columns) > 0
                validation_method = "len() check"
            except Exception as e1:
                try:
                    has_data = not df_candidate.empty
                    validation_method = ".empty check"
                except Exception as e2:
                    try:
                        has_data = df_candidate.shape[0] > 0
                        validation_method = ".shape check"
                    except Exception as e3:
                        # If all validation methods fail, try to access the data directly
                        try:
                            # Try to get the first few rows to test if data is accessible
                            _ = df_candidate.head(1)
                            has_data = True
                            validation_method = ".head() access test"
                        except Exception as e4:
                            logging.warning(f"{operation_name}: All validation methods failed: len={e1}, empty={e2}, shape={e3}, head={e4}")
                            # As a last resort, assume we have data if we got this far
                            has_data = True
                            validation_method = "fallback assumption"
            
            logging.info(f"{operation_name}: DataFrame validation using {validation_method}: has_data={has_data}")
            
            if has_data:
                try:
                    return df_candidate.copy()
                except Exception as copy_err:
                    logging.warning(f"{operation_name}: Could not copy DataFrame, returning original: {copy_err}")
                    return df_candidate
            else:
                logging.warning(f"{operation_name}: DataFrame appears to be empty")
                return None
        else:
            logging.error(f"{operation_name}: Failed to convert to pandas DataFrame, got {type(df_candidate)}")
            return None
            
    except Exception as e:
        logging.error(f"{operation_name}: DataFrame conversion failed with error: {e}")
        return None

def _generate_csv_from_query_context(user_query: str) -> str | None:
    """Generate a basic CSV from the user query context when code regeneration fails."""
    try:
        agent = st.session_state.get("agent")
        dataset = st.session_state.get("cellminer_dataset")
        
        if not agent or not dataset:
            return None
        
        # Try to extract basic query components from user query
        query_lower = user_query.lower()
        
        # Basic fallback query based on common patterns
        if any(word in query_lower for word in ['expression', 'exp', 'gene']):
            fallback_query = f"""
            SELECT col_name, row_name, value, dataset, cell_line_tissue
            FROM {TABLE_NAME} 
            WHERE data_type = 'exp' 
            LIMIT 1000;
            """
        elif any(word in query_lower for word in ['drug', 'activity', 'ic50', 'act']):
            fallback_query = f"""
            SELECT col_name, row_name, value, dataset, cell_line_tissue, drug_moa
            FROM {TABLE_NAME} 
            WHERE data_type = 'act' 
            LIMIT 1000;
            """
        else:
            # Generic fallback
            fallback_query = f"""
            SELECT col_name, row_name, value, data_type, dataset
            FROM {TABLE_NAME} 
            LIMIT 1000;
            """
        
        # Execute the fallback query
        fallback_df = None
        try:
            if hasattr(dataset, "_loader") and hasattr(dataset._loader, "execute_sql_query"):
                fallback_df = dataset._loader.execute_sql_query(fallback_query)
            elif hasattr(agent, "execute_sql_query"):
                fallback_df = agent.execute_sql_query(fallback_query)
            else:
                return None
        except Exception as query_err:
            logging.warning(f"Fallback query execution failed: {query_err}")
            return None
        
        # Enhanced DataFrame validation for VirtualDataFrame and other types
        # Use explicit None check to avoid VirtualDataFrame truthiness issues
        try:
            if fallback_df is not None:
                converted_df = _safe_dataframe_conversion(fallback_df, "Fallback query generation")
                if converted_df is not None:
                    _buf = io.StringIO()
                    converted_df.to_csv(_buf, index=False)
                    return _buf.getvalue()
        except Exception as conversion_err:
            logging.warning(f"Fallback DataFrame conversion failed: {conversion_err}")
            return None
            
    except Exception as e:
        logging.warning("Could not generate fallback CSV from query context: %s", e)
    
    return None

def _regenerate_dataframe_from_code(captured_code: str):
    """Attempt to re-execute PandasAI's captured code and return the DataFrame named `data`.

    If the execution fails or `data` is not produced, the function returns None. The code is
    executed in an isolated namespace to avoid side-effects on the main app."""
    if not captured_code:
        return None

    import pandas as _pd  # Local import to avoid polluting global namespace
    import types as _types
    import io as _io
    import sys as _sys
    import logging as _logging

    # Prepare an isolated globals / locals dict so that we don't leak variables
    _globals: dict[str, object] = {
        "__name__": "pandasai_exec_sandbox",
        "__doc__": None,
        "__package__": None,
        "__loader__": None,
        "__spec__": None,
        "__builtins__": __builtins__,  # allow built-ins
        # Add common imports that PandasAI code might use
        "pd": _pd,
        "pandas": _pd,
        "plt": plt,
        "matplotlib": plt.matplotlib,
        "sns": sns,
        "seaborn": sns,
        "np": np,
        "numpy": np,
        "os": os,
        "io": _io,
        "sys": _sys
    }

    # 👉 Inject a thin wrapper around the real dataset query helper so that any
    # `execute_sql_query(sql_query)` call inside the captured code works when
    # we re-run it in this isolated namespace.  This dramatically improves the
    # success rate of regenerating the intermediate DataFrame which powers the
    # "Export Intermediate Data" download button.
    import streamlit as _st

    def _sandbox_execute_sql_query(_sql: str, download_csv: bool = False):  # noqa: D401 - simple bridge
        """Proxy SQL execution through the live PandasAI dataset (read-only)."""
        try:
            _agent = _st.session_state.get("agent")
            _dataset = _st.session_state.get("cellminer_dataset")

            result = None
            # Preferred: dataset loader has an execute_sql_query helper
            if _dataset is not None and hasattr(_dataset, "_loader") and hasattr(_dataset._loader, "execute_sql_query"):
                result = _dataset._loader.execute_sql_query(_sql)  # type: ignore[attr-defined]

            # Fallback: if the Agent exposes a similar helper
            elif _agent is not None and hasattr(_agent, "execute_sql_query"):
                result = _agent.execute_sql_query(_sql)  # type: ignore[attr-defined]
                
            # Convert result to pandas DataFrame if it's not already
            if result is not None:
                converted_result = _safe_dataframe_conversion(result, "Sandbox SQL execution")
                if converted_result is not None:
                    return converted_result
                    
        except Exception as _proxy_err:
            logging.warning("Proxy execute_sql_query failed: %s", _proxy_err)
        # Graceful fallback - return empty DataFrame to avoid breaking the exec
        return _pd.DataFrame()

    # Make available to the executed code
    _globals["execute_sql_query"] = _sandbox_execute_sql_query

    _locals: dict[str, object] = {}

    try:
        exec(captured_code, _globals, _locals)  # nosec - code originates from the user-initiated LLM
        # Prefer locals first, then globals
        _df_candidate = _locals.get("data") or _globals.get("data")
        
        # Enhanced DataFrame validation and conversion
        if _df_candidate is not None:
            converted_df = _safe_dataframe_conversion(_df_candidate, "Code execution regeneration")
            if converted_df is not None:
                return converted_df
    except Exception as _exec_err:
        logging.warning("Could not regenerate DataFrame from captured code: %s", _exec_err)
    return None

def _embed_svg_base64(_svg_str: str, _unique_key: str, _height: int = 650):
    """Embed raw SVG string as a base64-encoded <img> for reliable rendering."""
    _encoded = base64.b64encode(_svg_str.encode("utf-8")).decode()
    _html_img = (
        f'<div style="width:100%;text-align:center;">'
        f'<img src="data:image/svg+xml;base64,{_encoded}" '
        f'style="max-width:100%;height:auto;"/>'
        f'</div>'
    )
    components.html(_html_img, height=_height)

# Fast initialiser - registers PostgreSQL-backed dataset + LLM agent (cached)
@st.cache_resource(show_spinner="🔗 Connecting to PostgreSQL & preparing dataset …")
def init_postgres_agent(dataset_path: str = DATASET_PATH, table_name: str = TABLE_NAME):
    """Create or attach to a PostgreSQL-backed PandasAI dataset + Agent."""

    # Purge the datasets/postgres folder before initializing the dataset
    import shutil, logging as _logging
    base_dir = os.path.dirname(os.path.abspath(__file__))
    target_dir = os.path.join(base_dir, "datasets", "postgres")
    if os.path.exists(target_dir):
        try:
            shutil.rmtree(target_dir)
        except Exception as purge_err:
            _logging.warning("Could not delete %s: %s", target_dir, purge_err)

    # All data is now in the cloud PostgreSQL database; no parquet needed
    ds_info = create_postgres_dataset(
        table_name=table_name,
        dataset_path=dataset_path,
    )
    dataset = ds_info["dataset"]
    
    # 🔑 FlashText synonym harmonization for cell line names
    try:
        # Check if FlashText is available
        try:
            from flashtext import KeywordProcessor
            FLASHTEXT_AVAILABLE = True
        except ImportError:
            FLASHTEXT_AVAILABLE = False
            print("⚠️ FlashText not available - skipping synonym harmonization")
            raise ImportError("FlashText not available")
        
        if FLASHTEXT_AVAILABLE:
            # Build keyword processor for cell line synonyms from PostgreSQL
            cell_line_kp = build_cell_line_keyword_processor(
                source="postgres",
                postgres_table=table_name,
                postgres_schema="public"
            )
            
            # Build keyword processor for drug synonyms from PostgreSQL
            drug_kp = build_drug_keyword_processor(
                source="postgres",
                postgres_table=table_name,
                postgres_schema="public"
            )
        
        # Store both keyword processors in session state for query harmonization
        if 'cell_line_kp' not in st.session_state:
            st.session_state.cell_line_kp = cell_line_kp
            print("✅ Cell line keyword processor stored in session state")
        if 'drug_kp' not in st.session_state:
            st.session_state.drug_kp = drug_kp
            print("✅ Drug keyword processor stored in session state")
        
        # Test FlashText functionality
        print("🧪 Testing FlashText synonym functionality...")
        test_flashtext_synonyms()
            
    except Exception as e:
        print(f"⚠️ FlashText synonym harmonization failed: {e}")
        print("ℹ️ Continuing without synonym harmonization")
        # Ensure keyword processors are not in session state if harmonization failed
        for key in ['cell_line_kp', 'drug_kp']:
            if key in st.session_state:
                del st.session_state[key]

    # 2️⃣ Enhanced LLM configuration with validation and automatic fallback
    
    # Validate Azure OpenAI configuration first
    config_issues = validate_azure_openai_config()
    if config_issues:
        raise ValueError(f"Azure OpenAI configuration issues: {'; '.join(config_issues)}")
    
    # Test connection before proceeding, with automatic API version fallback
    connection_ok, connection_msg = test_azure_openai_connection()
    working_api_version = os.getenv("AZURE_OPENAI_API_VERSION")
    
    if not connection_ok:
        # Try to find a working API version automatically
        print(f"⚠️ Connection failed with {working_api_version}: {connection_msg}")
        print("🔍 Attempting automatic API version fallback...")
        
        working_api_version = test_api_versions_for_fallback()
        
        if working_api_version:
            print(f"✅ Found working API version: {working_api_version}")
            # Update environment for this session
            os.environ["AZURE_OPENAI_API_VERSION"] = working_api_version
        else:
            raise ValueError(f"Azure OpenAI connection failed: {connection_msg}. No working API version found.")
    
    try:
        # Use the proper pandasai_openai.AzureOpenAI instead of custom wrapper
        from pandasai_openai import AzureOpenAI
        
        llm_instance = AzureOpenAI(
            azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            api_version=working_api_version,
            deployment_name=os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")
        )
        
    except ImportError:
        # Fallback to direct OpenAI client if pandasai_openai is not available
        try:
            from openai import AzureOpenAI
            
            # Create Azure OpenAI client directly with working API version
            azure_client = AzureOpenAI(
                azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
                api_key=os.getenv("AZURE_OPENAI_API_KEY"),
                api_version=working_api_version
            )
            
            # Create a proper wrapper that inherits from the expected base class
            from pandasai.llm.base import LLM
            
            class AzureOpenAIWrapper(LLM):
                def __init__(self, client, deployment_name):
                    self.client = client
                    self.deployment_name = deployment_name
                    super().__init__()
                    
                def chat(self, messages, **kwargs):
                    if isinstance(messages, str):
                        messages = [{"role": "user", "content": messages}]
                    elif isinstance(messages, list) and all(isinstance(m, str) for m in messages):
                        messages = [{"role": "user", "content": " ".join(messages)}]
                    
                    return self.client.chat.completions.create(
                        model=self.deployment_name,
                        messages=messages,
                        **kwargs
                    )
            
            llm_instance = AzureOpenAIWrapper(
                azure_client, 
                os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")
            )
            
        except ImportError:
            raise ImportError("Neither 'pandasai_openai' nor 'openai' packages are available")

    export_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "exports", "charts")
    os.makedirs(export_dir, exist_ok=True)

    try:
        agent_cfg = Config(
            llm=llm_instance,
            verbose=True,
            save_charts=True,
            save_charts_path=export_dir,
        )
    except TypeError:
        agent_cfg = Config(llm=llm_instance, verbose=True)

    try:
        from pandasai import set_default_config
        set_default_config(agent_cfg)
    except ImportError:
        pass

    agent = Agent(dataset, config=agent_cfg)

    return dataset, agent, llm_instance

# ---------------------------------------------------------------------------
# 🧠  PostgreSQL-optimised prompt builder (FlashText aware)
# ---------------------------------------------------------------------------

def add_query_to_history(query: str, response: str, response_type: str = "text"):
    """Add a query and its response to the session history for context."""
    if 'query_history' not in st.session_state:
        st.session_state.query_history = []
    
    # Keep only the last 5 queries to avoid token limits
    if len(st.session_state.query_history) >= 5:
        st.session_state.query_history.pop(0)
    
    # Clean and truncate response for storage
    if response_type == "dataframe":
        # For DataFrames, store summary info instead of full data
        if isinstance(response, pd.DataFrame):
            summary = f"DataFrame with {len(response)} rows and {len(response.columns)} columns. Columns: {list(response.columns)[:10]}"
            if len(response.columns) > 10:
                summary += f" and {len(response.columns) - 10} more columns"
        else:
            summary = str(response)[:500] + "..." if len(str(response)) > 500 else str(response)
    elif response_type == "plot":
        summary = f"Generated visualization/plot: {response}"
    else:
        # For text responses, truncate if too long
        summary = response[:1000] + "..." if len(response) > 1000 else response
    
    st.session_state.query_history.append({
        "query": query,
        "response": summary,
        "type": response_type,
        "timestamp": time.time()
    })

def build_context_from_history() -> str:
    """Build context string from previous query history."""
    if not st.session_state.get('query_history'):
        return ""
    
    context_parts = ["PREVIOUS QUERY CONTEXT (for reference only):"]
    
    for i, entry in enumerate(st.session_state.query_history[-3:], 1):  # Use last 3 queries
        context_parts.append(f"\nQuery {i}: {entry['query']}")
        context_parts.append(f"Response {i}: {entry['response']}")
        context_parts.append(f"Type: {entry['type']}")
    
    context_parts.append("\n" + "="*60 + "\n")
    
    return "\n".join(context_parts)

def harmonize_user_query_synonyms(user_question: str, cell_line_kp=None, drug_kp=None) -> str:
    """Apply FlashText synonym harmonization to user queries containing cell line names and drug names."""
    harmonized_question = user_question
    
    try:
        # Check if FlashText is available
        try:
            from flashtext import KeywordProcessor
            FLASHTEXT_AVAILABLE = True
        except ImportError:
            FLASHTEXT_AVAILABLE = False
            print("⚠️ FlashText not available for query harmonization")
            return user_question
        
        if not FLASHTEXT_AVAILABLE:
            return user_question
        
        # Apply cell line synonym replacement
        if cell_line_kp:
            original_question = harmonized_question
            harmonized_question = cell_line_kp.replace_keywords(harmonized_question)
            if harmonized_question != original_question:
                print(f"🔄 Cell line query harmonized: '{original_question}' → '{harmonized_question}'")
        
        # Apply drug synonym replacement
        if drug_kp:
            original_question = harmonized_question
            harmonized_question = drug_kp.replace_keywords(harmonized_question)
            if harmonized_question != original_question:
                print(f"🔄 Drug query harmonized: '{original_question}' → '{harmonized_question}'")
        
        return harmonized_question
    except Exception as e:
        print(f"⚠️ Query synonym harmonization failed: {e}")
        return user_question

def test_flashtext_synonyms():
    """Test function to verify FlashText synonym functionality."""
    try:
        # Check if FlashText is available
        try:
            from flashtext import KeywordProcessor
            FLASHTEXT_AVAILABLE = True
        except ImportError:
            FLASHTEXT_AVAILABLE = False
            print("❌ FlashText is not installed")
            return False
        
        if not FLASHTEXT_AVAILABLE:
            return False
        
        # Create a test keyword processor
        kp = KeywordProcessor(case_sensitive=False)
        kp.add_keyword("22RV1", "CVCL_0033")
        kp.add_keyword("22RV1_PROS", "CVCL_0033")
        
        # Test synonym replacement
        test_query = "Show me expression data for 22RV1 cell line"
        harmonized = kp.replace_keywords(test_query)
        
        print("🧪 FlashText Test Results:")
        print(f"Original: {test_query}")
        print(f"Harmonized: {harmonized}")
        print(f"✅ FlashText is working correctly")
        return True
        
    except Exception as e:
        print(f"❌ FlashText test failed: {e}")
        return False

def build_enhanced_prompt(user_question: str) -> str:
    """Return a curated system prompt optimised for PostgreSQL querying."""

    # Use the actual table name dynamically
    table_name = TABLE_NAME
    
    # Get context from previous queries
    context_history = build_context_from_history()

    return textwrap.dedent(
        f"""
        **SYSTEM INSTRUCTIONS - READ CAREFULLY**
        
        {context_history}
        
        You are an expert data-scientist working inside a Streamlit chat assistant. The full
        CellMinerCDB dataset is available via a PostgreSQL connection managed by PandasAI.
        
        DATABASE OVERVIEW
        • Physical table: public.{table_name}
        • PandasAI table to use in every SQL query: `{table_name}`
        
        CRITICAL RULES
        1.  Produce exactly ONE well-formed SQL query and pass it verbatim to `execute_sql_query()`.
        2.  Always reference the table `{table_name}`.
        3.  Compose complex logic with CTEs (e.g., `WITH exp_data AS (...)`).
        4.  Filter early by `data_type` and a canonical ID (`row_name` or `col_name`) for performance.
        5.  **Handle Duplicates**: Some cell lines have multiple measurements (technical replicates).
            To avoid errors and get accurate results, you MUST aggregate values. Use `AVG(value)` and
            `GROUP BY` the canonical identifiers (`col_name`, `row_name`).
        6.  **Joins**: When combining data (e.g., expression and drug activity), always join on the
            canonical cell line identifier, which is `col_name`.
        7.  Guard against empty results: `if len(data) == 0: return "No matching rows found."`.
        8.  Save all plots to `exports/charts/` and return the path as `{{"type":"plot","value":"<path>"}}`.
        9.  For tabular answers, summarise with `.head()` or descriptive statistics.
        
        DATA MODEL (long format with Canonical IDs)
        ───────────────────────────────────────────────────────────────────────────
        column               | description                                         | example
        ---------------------|-----------------------------------------------------|-------------------------------
        col_name (TEXT)      | **Canonical Cell Line ID** (Cellosaurus Accession)  | 'CVCL_1384'
        row_name (TEXT)      | **Canonical Gene/Drug ID** (Gene Symbol or PubChem CID) | 'TP53' or '5281'
        data_type (TEXT)     | Measurement type ('exp' or 'act')                   | 'exp'
        value (NUMERIC)      | The measurement value (e.g., expression level)      | 1.23
        dataset (TEXT)       | Source database (e.g., 'GDSC', 'CCLE')              | 'CCLE'
        cell_line_tissue     | Tissue of origin (lowercase)                        | 'prostate'
        drug_moa (TEXT)      | Drug's Mechanism of Action (for 'act' data)         | 'Topoisomerase inhibitor'
        original_cell_name   | Original cell line name from source data            | 'MCF7'
        original_row_name    | Original gene/drug name from source data            | 'Paclitaxel'
        
        #️⃣  EXAMPLE QUERIES (Use these patterns)
        
        -- Single gene expression for TP53, properly aggregated
        ```sql
        SELECT
            col_name AS cellosaurus_accession,
            AVG(value) AS avg_tp53_expression
        FROM {table_name}
        WHERE data_type = 'exp' AND row_name = 'TP53'
        GROUP BY col_name
        ORDER BY avg_tp53_expression DESC
        LIMIT 10;
        ```
        
        -- Drug-gene correlation (Paclitaxel IC50 vs. MDM2 expression), properly aggregated and joined
        -- Note: The user's query "Paclitaxel" was pre-processed to its PubChem CID '5281'.
        ```sql
        WITH exp_data AS (
            SELECT
                col_name,
                AVG(value) AS mdm2_expression
            FROM {table_name}
            WHERE data_type = 'exp' AND row_name = 'MDM2'
            GROUP BY col_name
        ),
        act_data AS (
            SELECT
                col_name,
                AVG(value) AS paclitaxel_ic50
            FROM {table_name}
            WHERE data_type = 'act' AND row_name = '5281' -- PubChem CID for Paclitaxel
            GROUP BY col_name
        )
        SELECT
            exp_data.col_name AS cellosaurus_accession,
            exp_data.mdm2_expression,
            act_data.paclitaxel_ic50
        FROM exp_data
        JOIN act_data ON exp_data.col_name = act_data.col_name
        WHERE act_data.paclitaxel_ic50 IS NOT NULL AND exp_data.mdm2_expression IS NOT NULL;
        ```
        
        User question: {user_question}
        """
    )

def add_response_to_session(response, user_query: str = ""):
    """
    Process the agent's response and add it to the chat history in session_state.
    This function does not render any UI elements directly. It prepares the
    response to be rendered by the main message display loop.
    """
    # ── Check for "No matching rows" response and replace ----------------
    if isinstance(response, str) and "No matching rows" in response:
        response = "No matching rows found. You can also try searching at cancer.gov."
    elif isinstance(response, dict) and "No matching rows" in response.get('value', ''):
        response['value'] = "No matching rows found. You can also try searching at cancer.gov."
    elif hasattr(response, 'value') and "No matching rows" in str(response.value):
        response.value = "No matching rows found. You can also try searching at cancer.gov."
    
    # ── Normalise payload ------------------------------------------------
    chart_path: str | None = None
    payload = None
    if isinstance(response, dict):
        payload = response
    elif hasattr(response, "value") and isinstance(response.value, dict):
        payload = response.value  # legacy object wrapper

    if payload and payload.get("type") == "plot":
        chart_path = payload.get("value")
    elif hasattr(response, "value") and isinstance(response.value, str):
        chart_path = response.value
    elif isinstance(response, str):
        _looks_like_path = (
            (os.sep in response or response.endswith(('.png', '.svg', '.jpg', '.jpeg')))
            and len(response.split()) == 1
        )
        if _looks_like_path:
            chart_path = response.strip()

    # ── Handle plot response ---------------------------------------------
    if chart_path:
        base, _ext = os.path.splitext(chart_path)
        svg_candidate = base + ".svg" if not chart_path.endswith(".svg") else chart_path
        display_path = svg_candidate if os.path.exists(svg_candidate) else chart_path

        if os.path.exists(display_path):
            # Use a unique key to handle multiple generations of the same plot
            key = f"plot_{os.path.basename(display_path)}_{time.time()}"
            
            with open(display_path, "r", encoding="utf-8") as _f:
                svg_raw = _f.read()

            # ── Get CSV data - prioritize if it was generated during the process ──
            captured_code = st.session_state.code_capture_handler.get_captured_code()
            csv_data = None
            csv_error = None
            
            # Check if CSV data was already generated during the CSV-first plotting process
            csv_first_data = getattr(st.session_state, 'csv_first_data', None)
            if csv_first_data:
                csv_data = csv_first_data
                csv_error = None
                st.session_state.csv_first_data = None  # Clear after use
                logging.info("✅ Using CSV data from CSV-first plotting process")
            else:
                # Fallback to existing CSV generation methods
                # Method 1: Try to regenerate DataFrame from captured code
                if captured_code:
                    try:
                        _regen_df = _regenerate_dataframe_from_code(captured_code)
                        converted_df = _safe_dataframe_conversion(_regen_df, "Method 1 - Code regeneration")
                        if converted_df is not None:
                            _buf = io.StringIO()
                            converted_df.to_csv(_buf, index=False)
                            csv_data = _buf.getvalue()
                            logging.info("Method 1 successful: CSV generated from regenerated code")
                    except Exception as e:
                        csv_error = f"Code regeneration failed: {e}"
                        logging.error(f"Method 1 failed: {e}")
                
                # Method 2: If code regeneration failed, try to extract and execute SQL directly
                if csv_data is None and captured_code:
                    try:
                        extracted_sql = _extract_sql_from_captured_code(captured_code)
                        if extracted_sql:
                            # Log the extracted SQL for debugging
                            logging.info(f"Extracted SQL query: {extracted_sql[:200]}...")
                            
                            # Save the extracted SQL to file with user context
                            st.session_state.code_capture_handler.save_sql_with_context(extracted_sql, user_query)
                            
                            agent = st.session_state.get("agent")
                            dataset = st.session_state.get("cellminer_dataset")
                            
                            sql_df = None
                            try:
                                if dataset and hasattr(dataset, "_loader") and hasattr(dataset._loader, "execute_sql_query"):
                                    logging.info("Executing SQL via dataset._loader.execute_sql_query")
                                    sql_df = dataset._loader.execute_sql_query(extracted_sql)
                                elif agent and hasattr(agent, "execute_sql_query"):
                                    logging.info("Executing SQL via agent.execute_sql_query")
                                    sql_df = agent.execute_sql_query(extracted_sql)
                            except Exception as sql_exec_err:
                                logging.error(f"SQL execution failed: {sql_exec_err}")
                                sql_df = None
                            
                            # Enhanced DataFrame validation for VirtualDataFrame and other types
                            # Use explicit None check to avoid VirtualDataFrame truthiness issues
                            try:
                                if sql_df is not None:
                                    converted_df = _safe_dataframe_conversion(sql_df, "Method 2 - SQL extraction")
                                    if converted_df is not None:
                                        _buf = io.StringIO()
                                        converted_df.to_csv(_buf, index=False)
                                        csv_data = _buf.getvalue()
                                        logging.info("Method 2 successful: CSV generated from extracted SQL")
                                        if csv_error:
                                            csv_error = "Used extracted SQL query for CSV generation"
                                        else:
                                            csv_error = None  # Clear any previous errors
                                    else:
                                        logging.warning("Method 2 failed: DataFrame conversion returned None")
                            except Exception as conversion_err:
                                logging.error(f"Method 2 DataFrame conversion failed: {conversion_err}")
                                if csv_error:
                                    csv_error += f"; DataFrame conversion failed: {conversion_err}"
                                else:
                                    csv_error = f"DataFrame conversion failed: {conversion_err}"
                        else:
                            logging.warning("No SQL query could be extracted from captured code")
                    except Exception as e:
                        logging.error(f"SQL extraction process failed: {e}")
                        if csv_error:
                            csv_error += f"; SQL extraction failed: {e}"
                        else:
                            csv_error = f"SQL extraction failed: {e}"
                
                # Method 3: If all else fails, generate basic CSV from query context
                if csv_data is None and user_query:
                    try:
                        fallback_csv = _generate_csv_from_query_context(user_query)
                        if fallback_csv:
                            csv_data = fallback_csv
                            if csv_error:
                                csv_error += "; Using contextual fallback data"
                            else:
                                csv_error = "Using contextual fallback data (original plot data unavailable)"
                            logging.info("Method 3 successful: CSV generated from query context")
                    except Exception as e:
                        if csv_error:
                            csv_error += f"; Fallback generation failed: {e}"
                        else:
                            csv_error = f"All CSV generation methods failed: {e}"
                        logging.error(f"Method 3 failed: {e}")
                
                # Method 3.5: Try to access PandasAI's last execution result directly
                if csv_data is None:
                    try:
                        agent = st.session_state.get("agent")
                        if agent and hasattr(agent, '_last_result'):
                            last_result = agent._last_result
                            if last_result is not None:
                                converted_df = _safe_dataframe_conversion(last_result, "Method 3.5 - Agent last result")
                                if converted_df is not None:
                                    _buf = io.StringIO()
                                    converted_df.to_csv(_buf, index=False)
                                    csv_data = _buf.getvalue()
                                    if csv_error:
                                        csv_error += "; Using agent's last execution result"
                                    else:
                                        csv_error = "Using agent's last execution result"
                                    logging.info("Method 3.5 successful: CSV generated from agent's last result")
                    except Exception as e:
                        logging.warning(f"Method 3.5 failed: Could not access agent's last result: {e}")
                
                # Method 4: Last resort - create a minimal informational CSV
                if csv_data is None:
                    try:
                        # Create a minimal CSV with information about the plot
                        minimal_df = pd.DataFrame({
                            'info': ['Plot generated but underlying data could not be extracted'],
                            'plot_file': [os.path.basename(display_path)],
                            'user_query': [user_query or 'N/A'],
                            'timestamp': [time.strftime('%Y-%m-%d %H:%M:%S')]
                        })
                        _buf = io.StringIO()
                        minimal_df.to_csv(_buf, index=False)
                        csv_data = _buf.getvalue()
                        if csv_error:
                            csv_error += "; Providing minimal plot metadata instead"
                        else:
                            csv_error = "Data extraction failed - providing minimal plot metadata"
                    except Exception as e:
                        csv_error = f"All CSV generation methods failed, including minimal fallback: {e}"

            st.session_state[key] = {
                "svg_content": svg_raw,
                "file_name": os.path.basename(display_path),
                "captured_code": captured_code,
                "csv_data": csv_data,
                "csv_error": csv_error,
            }
            
            st.session_state.messages.append({
                "role": "assistant",
                "content": "✅ Analysis complete! Here's your visualization.",
                "plot_key": key
            })
            
            # Add to query history
            if user_query:
                add_query_to_history(user_query, display_path, "plot")
            return

    # ── Handle DataFrame and string fallbacks -----------------------------
    actual_response = response
    if isinstance(response, dict) and 'value' in response:
        actual_response = response['value']

    if isinstance(actual_response, pd.DataFrame):
        st.session_state.messages.append({
            "role": "assistant",
            "content": "✅ Here is the data you requested:",
            "dataframe": actual_response
        })
        # Add to query history
        if user_query:
            add_query_to_history(user_query, actual_response, "dataframe")
        elif isinstance(actual_response, str):
            # Check for "No matching rows" and replace with enhanced message
            if "No matching rows" in actual_response:
                actual_response = "No matching rows found. You can also try searching at cancer.gov."
            
            # Check if we have captured code with SQL for this response
            captured_code = None
            if 'code_capture_handler' in st.session_state:
                captured_code = st.session_state.code_capture_handler.get_captured_code()
            
            st.session_state.messages.append({
                "role": "assistant",
                "content": actual_response,
                "has_sql": captured_code and _extract_sql_from_captured_code(captured_code) is not None,
                "captured_code": captured_code
            })
            # Add to query history
            if user_query:
                add_query_to_history(user_query, actual_response, "text")
    else:
        response_str = str(actual_response)
        # Check for "No matching rows" and replace with enhanced message
        if "No matching rows" in response_str:
            response_str = "No matching rows found. You can also try searching at cancer.gov."
        
        st.session_state.messages.append({
            "role": "assistant",
            "content": response_str,
        })
        # Add to query history
        if user_query:
            add_query_to_history(user_query, response_str, "text")

# Header
st.title("CellMinerCDB Data Analysis Chatbot")
st.markdown("""
This chatbot provides **dual query routing** for comprehensive cancer research support:

**🤖 Direct LLM Queries**: General knowledge questions about cancer biology, genomics, and research methods  
**🔬 @agent Queries**: Live data analysis of the complete CellMinerCDB dataset (~50M rows)

**Dataset Features:**
- Real-time analysis via remote Azure PostgreSQL backend with canonical IDs
- Drug activity screening data across cancer cell lines
- Gene expression, mutation, and copy number variation profiles
- Cross-database cell line and drug annotation integration from CCLE and GDSC

**How to Use:**
- **General questions**: Type normally (e.g., "What is cancer genomics?")
- **Data analysis**: Start with `@agent` (e.g., "@agent plot tp53 versus mdm2 expression in gdsc")
""")

# Sidebar with information
with st.sidebar:
    st.markdown("## Dual Query System")
    st.markdown("""
    **🤖 Direct LLM Mode**
    - General knowledge questions
    - No `@agent` prefix needed
    - Cancer biology, research methods
    - Fast responses
    
    **🔬 Agent Mode (@agent)**
    - Requires `@agent` prefix
    - Live dataset analysis
    - SQL queries & visualizations
    - ~50M rows of CellMinerCDB data
    """)
    
    st.markdown("## About this Dataset")
    st.markdown("""
    **CellMinerCDB Combined Dataset**
    - **~50 million rows** of integrated data
    - **Drug activity** (IC50 values)
    - **Gene expression** profiles
    - **Canonical IDs**: Uses Cellosaurus for cell lines and PubChem for drugs to ensure data integrity.
    """)
    
    st.markdown("## Sample Questions")
    st.markdown("""
    **🤖 Direct LLM Queries (General Knowledge):**
    - "What is cancer genomics?"
    - "Explain drug resistance mechanisms"
    
    **🔬 @agent Queries (Dataset Analysis):**
    
    **Drug Discovery:**
    - "@agent Which drugs show the highest potency against breast cancer cell lines?"
    - "@agent Find drugs with similar activity patterns to cisplatin"
    
    **Gene Analysis:**
    - "@agent Which genes are most highly expressed in melanoma cell lines?"
    - "@agent Show copy number alterations in TP53 across all cell lines"
    
    **Biomarker Discovery:**
    - "@agent Correlate EGFR expression with erlotinib sensitivity"
    - "@agent plot tp53 versus mdm2 expression in gdsc"
    """)
    
    # Load data button
    if st.button("🚀 Initialize CellMinerCDB Dataset"):
        # ------------------------------------------------------------------
        # Detailed initialisation with live progress + row-count information
        # ------------------------------------------------------------------
        progress_bar = st.progress(0, text="🔗 Connecting to PostgreSQL …")
        status_placeholder = st.empty()

        # ------------------------------------------------------------------
        # Quick connectivity + table-existence check (5-second timeout) so that
        # users get immediate feedback instead of waiting minutes if the DB is
        # unreachable.  This pre-flight adds negligible latency on success but
        # fails fast on network issues.
        # ------------------------------------------------------------------

        import psycopg2
        import psycopg2.errors

        # --- Begin: Use .env credentials if present ---
        # This logic is improved to correctly parse the DBEAVER_URL
        def parse_db_url(url):
            from urllib.parse import urlparse
            if not url:
                return {}
            if not url.startswith('postgresql://'):
                url = f'postgresql://{url}'
            parsed = urlparse(url)
            return {
                "host": parsed.hostname,
                "port": parsed.port,
            }

        # Strictly use only the required env vars
        db_url = os.getenv("DBEAVER_URL")
        database = os.getenv("DBEAVER_DATABASE")
        user = os.getenv("DBEAVER_USERNAME")
        password = os.getenv("DBEAVER_PASSWORD")

        db_params = parse_db_url(db_url) if db_url else {}
        host = db_params.get("host")
        port = db_params.get("port")

        # Validate all required fields
        missing = []
        if not db_url: missing.append("DBEAVER_URL")
        if not database: missing.append("DBEAVER_DATABASE")
        if not user: missing.append("DBEAVER_USERNAME")
        if not password: missing.append("DBEAVER_PASSWORD")
        if not host: missing.append("host (from DBEAVER_URL)")
        if not port: missing.append("port (from DBEAVER_URL)")

        if missing:
            status_placeholder.error(
                f"❌ Database connection info missing: {', '.join(missing)}. "
                "Please set these in your .env file."
            )
            st.sidebar.error("PostgreSQL credentials missing - aborting initialisation.")
            st.stop()

        status_placeholder.info(f"Connecting to {host}:{port} as user '{user}' …")

        test_conn = None  # connection placeholder

        try:
            test_conn = psycopg2.connect(
                host=host,
                port=port,
                user=user,
                password=password,
                dbname=database,
                connect_timeout=int(os.getenv("PG_CONNECT_TIMEOUT", "10")),
            )
        except psycopg2.OperationalError as exc:
            progress_bar.progress(100, text="❌ Database connection failed")
            status_placeholder.error(
                f"Cannot connect to PostgreSQL ({exc}). Ensure the server is running and credentials in your .env file are correct."
            )
            st.sidebar.error("PostgreSQL unreachable - aborting initialisation.")
            st.stop()

        progress_bar.progress(10, text="✅ PostgreSQL reachable - checking table …")

        table_exists = False
        try:
            with test_conn.cursor() as cur:
                cur.execute(
                    """SELECT EXISTS (
                            SELECT 1 FROM information_schema.tables 
                            WHERE table_schema = 'public' AND table_name = %s
                        );""",
                    (TABLE_NAME,),
                )
                table_exists = cur.fetchone()[0]
        except Exception as e:
            status_placeholder.error(f"Error checking for table: {e}")
            st.stop()
        finally:
            if test_conn:
                test_conn.close()

        if not table_exists:
            progress_bar.progress(100, text=f"❌ Table '{TABLE_NAME}' not found!")
            status_placeholder.error(
                f"The required table `public.{TABLE_NAME}` does not exist in the database `{database}`. "
                "Please ensure the table is created and the `TABLE_NAME` in your script or environment "
                "variables matches the table in your database."
            )
            st.stop()
        
        status_placeholder.success(f"✅ Table '{TABLE_NAME}' found. Initializing agent...")
        progress_bar.progress(20, text="🔄 Preparing PandasAI dataset …")

        try:
            # 1️⃣  Connect and register PandasAI dataset
            cellminer_dataset, agent, llm_instance = init_postgres_agent(dataset_path=DATASET_PATH, table_name=TABLE_NAME)

            st.session_state.agent = agent
            st.session_state.cellminer_dataset = cellminer_dataset
            st.session_state.llm_instance = llm_instance

            progress_bar.progress(60, text="📚 Registering dataset with PandasAI …")
            
            dataset_path = getattr(cellminer_dataset, 'path', DATASET_PATH)
            status_placeholder.info("Dataset registered, retrieving row estimate …")

            # 2️⃣  Quick row estimate from PostgreSQL system catalog using pg_class.reltuples
            rows_exact: int | None = None
            try:
                # First try the optimized pg_class.reltuples estimate
                rows_exact = get_table_row_estimate(host, port, user, password, database, TABLE_NAME)
                
                # Fallback to PandasAI loader method if available
                if rows_exact is None:
                    if getattr(cellminer_dataset, "_loader", None) and hasattr(cellminer_dataset._loader, "get_row_count"):
                        rows_exact = cellminer_dataset._loader.get_row_count()  # type: ignore[attr-defined]
            except Exception as count_exc:
                status_placeholder.warning(f"Row count estimate unavailable: {count_exc}")

            # 3️⃣  Finalise progress
            progress_bar.progress(100, text="✅ Dataset initialisation complete!")

            st.sidebar.success("🎉 CellMinerCDB (PostgreSQL) ready!")
            st.sidebar.markdown("### 📊 Dataset Info")
            if rows_exact is not None:
                st.sidebar.metric("Estimated Rows", f"{rows_exact:,}")
                row_msg = f"The table contains approximately **{rows_exact:,} rows**. "
            else:
                row_msg = ""
            
            welcome_msg = (
                "🔬 **CellMinerCDB dataset (PostgreSQL) is now ready for analysis!**\n\n" +
                row_msg +
                "I can help you explore large-scale drug and molecular data across cancer cell lines. "
                "What would you like to analyze?"
            )
            st.session_state.messages.append({"role": "assistant", "content": welcome_msg})
            st.rerun()

        except Exception as e:
            progress_bar.progress(100, text="❌ Initialisation failed")
            error_str = str(e)
            
            error_msg = f"❌ Error setting up dataset: {error_str}"
            status_placeholder.error(error_msg)
            st.sidebar.error(error_msg)
            st.sidebar.info("💡 Verify PostgreSQL credentials and table name in environment variables.")
            
            with st.expander("🔧 Troubleshooting Guide"):
                st.markdown(f"""
                **Error Details:** `{error_str}`
                
                **Common Solutions:**
                1. **Database Connection:**
                    - Ensure your PostgreSQL server is accessible from where you are running the app.
                    - Double-check all credentials in your `.env` file.
                
                2. **Permissions:**
                    - Verify the user has `SELECT` permissions on the `public.{TABLE_NAME}` table.
                
                3. **PandasAI Issues:**
                    - Try clearing the Streamlit cache using the button below.
                
                **Environment Variables to Check:**
                ```
                DBEAVER_URL={os.getenv("DBEAVER_URL")}
                DBEAVER_DATABASE={os.getenv("DBEAVER_DATABASE")}
                DBEAVER_USERNAME={os.getenv("DBEAVER_USERNAME")}
                DBEAVER_PASSWORD={'***' if os.getenv('DBEAVER_PASSWORD') else ''}
                TABLE_NAME={TABLE_NAME}
                ```
                """)
                
                if st.button("🔄 Clear Cache", key="error_clear_cache"):
                    st.cache_resource.clear()
                    st.success("✅ Cache cleared! Please try initializing again.")
                    st.rerun()
    
    # Add a standalone cache clear button for general troubleshooting
    st.sidebar.markdown("---")
    st.sidebar.markdown("### 🔧 Troubleshooting")
    if st.sidebar.button("🗑️ Clear All Cache"):
        st.cache_resource.clear()
        # Clear dataset-related session state
        for key in ['agent', 'cellminer_dataset', 'llm_instance']:
            if key in st.session_state:
                del st.session_state[key]
        st.sidebar.success("✅ All cache cleared!")
        st.rerun()
        
    if st.sidebar.button("🔄 Reset Messages"):
        st.session_state.messages = []
        st.session_state.query_history = []  # Also clear query history
        st.sidebar.success("✅ Chat history and query context cleared!")
        st.rerun()
    

    

    

    


# Display chat messages from history
for i, message in enumerate(st.session_state.messages):
    with st.chat_message(message["role"]):
        # Check if this is a timing message and style it differently
        if message.get("is_timing", False):
            st.markdown(f"<small style='color: #7f8c8d; font-style: italic;'>{message.get('content', '')}</small>", unsafe_allow_html=True)
        else:
            # Render content, which could be text, a plot, or a dataframe
            st.markdown(message.get("content", ""))

        if "plot_key" in message:
            plot_key = message["plot_key"]
            if plot_key in st.session_state:
                _plot_dict = st.session_state[plot_key]
                _embed_svg_base64(_plot_dict["svg_content"], plot_key)
                
                # Use columns for download buttons
                col1, col2, col3, col4 = st.columns(4)

                with col1:
                    st.download_button(
                        label="⬇️ Download SVG",
                        data=_plot_dict["svg_content"],
                        file_name=_plot_dict["file_name"],
                        mime="image/svg+xml",
                        key=f"download_svg_{plot_key}"
                    )
                
                captured_code = _plot_dict.get("captured_code")
                csv_data = _plot_dict.get("csv_data")
                csv_error = _plot_dict.get("csv_error")
                
                if captured_code:
                    _code_dl = clean_captured_code_for_download(captured_code, _plot_dict["file_name"])
                    with col2:
                        st.download_button(
                            label="⬇️ Download Plot Code",
                            data=_code_dl or captured_code,
                            file_name="recreate_plot.py",
                            mime="text/plain",
                            key=f"download_code_{plot_key}"
                        )
                    
                    # Add SQL Query download button
                    extracted_sql = _extract_sql_from_captured_code(captured_code)
                    if extracted_sql:
                        with col3:
                            st.download_button(
                                label="⬇️ Download SQL Query",
                                data=extracted_sql,
                                file_name=f"query_{plot_key}.sql",
                                mime="text/plain",
                                key=f"download_sql_{plot_key}"
                            )

                # Always show CSV download button if we have CSV data
                if csv_data:
                    with col4:
                        # Determine the appropriate label based on the type of CSV data
                        button_label = "⬇️ Download Data CSV"
                        if csv_error and ("minimal" in csv_error.lower() or "metadata" in csv_error.lower()):
                            button_label = "⬇️ Download Plot Info CSV"
                        elif csv_error and ("fallback" in csv_error.lower() or "contextual" in csv_error.lower()):
                            button_label = "⬇️ Download Sample CSV"
                        
                        st.download_button(
                            label=button_label,
                            data=csv_data,
                            file_name="data.csv",
                            mime="text/csv",
                            key=f"download_csv_{plot_key}",
                            help=csv_error if csv_error else "Download the data used to create this plot"
                        )
                else:
                    # If no CSV data available, try to regenerate as fallback
                    if captured_code:
                        _regen_df = _regenerate_dataframe_from_code(captured_code)
                        # Use safe conversion to handle VirtualDataFrame and other types
                        converted_regen_df = None
                        if _regen_df is not None:
                            converted_regen_df = _safe_dataframe_conversion(_regen_df, "Download button regeneration")
                        
                        if converted_regen_df is not None and not converted_regen_df.empty:
                            _buf = io.StringIO()
                            converted_regen_df.to_csv(_buf, index=False)
                            with col4:
                                st.download_button(
                                    label="⬇️ Download Data CSV",
                                    data=_buf.getvalue(),
                                    file_name="data.csv",
                                    mime="text/csv",
                                    key=f"download_csv_fallback_{plot_key}"
                                )
                        else:
                            # Show disabled button with explanation
                            with col4:
                                st.button(
                                    "❌ CSV Unavailable",
                                    disabled=True,
                                    key=f"download_csv_disabled_{plot_key}",
                                    help="CSV data could not be generated for this plot"
                                )
                    else:
                        # No captured code available
                        with col4:
                            st.button(
                                "❌ CSV Unavailable", 
                                disabled=True,
                                key=f"download_csv_nocode_{plot_key}",
                                help="No code was captured for this plot"
                            )
                
                # Show CSV error message if there was an issue
                if csv_error:
                    st.warning(f"⚠️ CSV Generation: {csv_error}")

        elif "dataframe" in message:
            df = message["dataframe"]
            st.dataframe(df, use_container_width=True)
            csv_buf = io.StringIO()
            df.to_csv(csv_buf, index=False)
            
            # Check if we have captured code with SQL for this response
            captured_code = None
            if 'code_capture_handler' in st.session_state:
                captured_code = st.session_state.code_capture_handler.get_captured_code()
            
            # Display download buttons in a layout that includes SQL if available
            if captured_code and _extract_sql_from_captured_code(captured_code):
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.download_button(
                        label="⬇️ Download CSV",
                        data=csv_buf.getvalue(),
                        file_name="pandasai_result.csv",
                        mime="text/csv",
                        key=f"download_df_{i}"  # Use index for unique key
                    )
                
                with col2:
                    # Add SQL Query download button for DataFrame responses
                    extracted_sql = _extract_sql_from_captured_code(captured_code)
                    if extracted_sql:
                        st.download_button(
                            label="⬇️ Download SQL Query",
                            data=extracted_sql,
                            file_name=f"query_df_{i}.sql",
                            mime="text/plain",
                            key=f"download_sql_df_{i}"
                        )
                
                with col3:
                    # Placeholder for potential future button
                    pass
            else:
                # Standard two-column layout if no SQL available
                col1, col2 = st.columns(2)
                with col1:
                    st.download_button(
                        label="⬇️ Download CSV",
                        data=csv_buf.getvalue(),
                        file_name="pandasai_result.csv",
                        mime="text/csv",
                        key=f"download_df_{i}"  # Use index for unique key
                    )
        
        # Handle text responses that might have associated SQL
        elif message.get("has_sql", False) and message.get("captured_code"):
            # This is a text response with associated SQL code
            captured_code = message["captured_code"]
            extracted_sql = _extract_sql_from_captured_code(captured_code)
            
            if extracted_sql:
                # Create columns for the SQL button and download
                col1, col2 = st.columns(2)
                
                with col1:
                    st.download_button(
                        label="⬇️ Download SQL Query",
                        data=extracted_sql,
                        file_name=f"query_text_{i}.sql",
                        mime="text/plain",
                        key=f"download_sql_text_{i}"
                    )
                
                with col2:
                    # Placeholder for potential future button
                    pass
        

        
        elif message.get("image"):
            st.image(message["image"], caption=message.get("caption", ""))

if prompt := st.chat_input("Ask a question about the CellMinerCDB dataset... (Use @agent for data analysis)"):
    st.session_state.messages.append({"role": "user", "content": prompt})

    start_time = time.time()
    
    # Check if query starts with @agent
    is_agent_query = prompt.startswith("@agent")
    
    # Clear any previous SQL display state for new query
    if 'current_query_sql' in st.session_state:
        del st.session_state['current_query_sql']
    
    if is_agent_query:
        agent_prompt = prompt[6:].strip()
        
        # 🔑 Apply FlashText synonym harmonization to user queries
        try:
            if 'cell_line_kp' in st.session_state and 'drug_kp' in st.session_state:
                original_prompt = agent_prompt
                agent_prompt = harmonize_user_query_synonyms(
                    agent_prompt, 
                    st.session_state.cell_line_kp, 
                    st.session_state.drug_kp
                )
                if agent_prompt != original_prompt:
                    st.info(f"🔄 Query harmonized: '{original_prompt}' → '{agent_prompt}'")
            else:
                print("ℹ️ No FlashText keyword processors available for query harmonization")
        except Exception as e:
            print(f"⚠️ Query synonym harmonization failed: {e}")
        
        if st.session_state.agent is None:
            add_response_to_session("Please initialize the dataset first by clicking '🚀 Initialize CellMinerCDB Dataset' in the sidebar.")
        else:
            with st.spinner("🔍 Querying CellMinerCDB (PostgreSQL) dataset…"):
                try:
                    st.session_state.code_capture_handler.clear_captured_code()
                    
                    # Check if this is a plot request and use CSV-first approach
                    if is_plot_request(agent_prompt):
                        logging.info("🎯 Plot request detected - using CSV-first approach")
                        with st.spinner("📊 Creating visualization (CSV-first approach)..."):
                            success, response = handle_csv_first_plotting(agent_prompt, st.session_state.agent)
                            if not success:
                                # Fallback to regular approach if CSV-first fails
                                logging.warning("CSV-first approach failed, falling back to regular approach")
                                enhanced_prompt = build_enhanced_prompt(agent_prompt)
                                response = st.session_state.agent.chat(enhanced_prompt)
                    else:
                        # Regular non-plot query
                        enhanced_prompt = build_enhanced_prompt(agent_prompt)
                        response = st.session_state.agent.chat(enhanced_prompt)
                    
                    # After agent response, check if any SQL was captured and save it
                    captured_code = st.session_state.code_capture_handler.get_captured_code()
                    if captured_code:
                        extracted_sql = _extract_sql_from_captured_code(captured_code)
                        if extracted_sql:
                            st.session_state.code_capture_handler.save_sql_with_context(extracted_sql, agent_prompt)
                            # Store the SQL for the current query to display it
                            st.session_state['current_query_sql'] = extracted_sql
                        
                    elapsed_time = time.time() - start_time
                    add_response_to_session(response, agent_prompt)  # Pass the user query
                    timing_msg = f"⏱️ Query completed in {elapsed_time:.2f} seconds"
                    st.session_state.messages.append({"role": "assistant", "content": timing_msg, "is_timing": True})
                except Exception as e:
                    elapsed_time = time.time() - start_time
                    error_msg = f"❌ Error processing request: {e}\n⏱️ Failed after {elapsed_time:.2f} seconds"
                    add_response_to_session(error_msg, agent_prompt)  # Pass the user query even for errors
    else:
        # Direct LLM query
        with st.spinner("🤖 Generating response using LLM…"):
            response_content = ""
            try:
                llm_direct = st.session_state.get("llm_instance")
                if llm_direct is None:
                    response_content = "Please initialize the dataset to enable both direct LLM queries and data analysis capabilities."
                else:
                    import sys
                    _sys_msg = (
                        "You are an expert biomedical researcher and data scientist. Provide helpful, "
                        "accurate information about bioinformatics, cancer research, genomics, and data analysis. "
                        "Keep responses concise but informative."
                    )
                    _messages = [{"role": "system", "content": _sys_msg}, {"role": "user", "content": prompt}]
                    # First try the llm_instance created during dataset initialisation.  Some
                    # versions of the pandasai_openai.AzureOpenAI wrapper do *not* expose a
                    # `.chat` helper, which results in an `AttributeError`.  In that case - or
                    # if any other runtime error occurs - we seamlessly fall back to a fresh
                    # Azure OpenAI client from the official `openai` SDK so that the user still
                    # receives a valid response instead of an error message.

                    try:
                        if hasattr(llm_direct, "chat"):
                            llm_resp = llm_direct.chat(_messages, max_tokens=800)
                            response_content = (
                                llm_resp.choices[0].message.content
                                if hasattr(llm_resp, "choices")
                                else str(llm_resp)
                            )
                        else:
                            raise AttributeError("llm_direct has no .chat attribute")
                    except Exception:
                        from openai import AzureOpenAI as _AzureOpenAIClient  # Local import for fallback

                        _fallback_client = _AzureOpenAIClient(
                            azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
                            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
                            api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
                        )

                        _resp = _fallback_client.chat.completions.create(
                            model=os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME"),
                            messages=_messages,
                            max_tokens=800,
                        )

                        response_content = _resp.choices[0].message.content
                        
                # Calculate elapsed time
                elapsed_time = time.time() - start_time
                
                # Add timing information to the response
                response_content += f"\n\n⏱️ Response generated in {elapsed_time:.2f} seconds"
                
            except Exception as e:
                elapsed_time = time.time() - start_time
                response_content = f"❌ Error processing LLM request: {e}\n⏱️ Failed after {elapsed_time:.2f} seconds"
                
            add_response_to_session(response_content, prompt)  # Pass the original prompt for direct LLM queries
    
    st.rerun()



# Footer
st.markdown("---")
st.markdown("""
<div style="text-align: center;">
    <p style="color: #7f8c8d; font-size: 0.8rem;">
        🧬 CellMinerCDB Analysis Powered by PandasAI 3.x & Azure PostgreSQL | Real-time querying of 50M+ data points
    </p>
    <p style="color: #7f8c8d; font-size: 0.7rem;">
        This chatbot can make mistakes. Please verify important information.
    </p>
</div>
""", unsafe_allow_html=True)
