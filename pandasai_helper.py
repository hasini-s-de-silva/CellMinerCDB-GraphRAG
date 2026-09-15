from __future__ import annotations
import os
import pandas as pd
import pandasai as pai
from typing import Dict, List, TYPE_CHECKING
import yaml
import re
import time
import logging
from logging.handlers import RotatingFileHandler
import getpass  # Added for OS-user fallback when 'postgres' role is absent
import psycopg2  # Moved up to follow PEP 8 import ordering
from psycopg2 import sql as _pg_sql
import pyarrow.parquet as pq
from sqlalchemy import create_engine
import io
from concurrent.futures import ThreadPoolExecutor, as_completed
import tempfile
import pyarrow.csv as pcsv
import psutil
import datetime
from dotenv import load_dotenv
from urllib.parse import urlparse

load_dotenv()

# -----------------------------------------------------------------------------
# Configure detailed logging to a rotating file (executed at import-time)
# -----------------------------------------------------------------------------
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "pandasai_helper.log")

_logger = logging.getLogger(__name__)
_logger.setLevel(logging.DEBUG)

# Avoid adding multiple handlers during interactive reloads (e.g., Streamlit)
if not _logger.handlers:
    _formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s - %(message)s")
    _handler = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3)
    _handler.setLevel(logging.DEBUG)
    _handler.setFormatter(_formatter)
    _logger.addHandler(_handler)
    # Propagate root logger to also use this handler (optional)
    logging.basicConfig(level=logging.INFO, handlers=[_handler])

# ---------------------------------------------------------------------------
# No DuckDB; Postgres only.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Import semantic-layer helpers - required in modern PandasAI.
# ---------------------------------------------------------------------------

try:
    from pandasai.data_loader.semantic_layer_schema import SemanticLayerSchema  # type: ignore
except Exception:  # pragma: no cover
    # If the import itself fails we set to None and rely on hard-coded maps
    SemanticLayerSchema = None  # type: ignore

# --------------------------
# Fallback mapping helpers
# --------------------------

_DEFAULT_ARROW_TO_PAI: dict[str, str] = {
    "string": "string",
    "large_string": "string",
    "double": "float",
    "float64": "float",
    "float32": "float",
    "int32": "integer",
    "int64": "integer",
    "int16": "integer",
    "bool": "boolean",
}


def _arrow_to_pai_map() -> dict:  # noqa: D401 - simple helper
    """Return the Arrow→PandasAI type map from the library or a fallback."""
    if SemanticLayerSchema is not None and hasattr(SemanticLayerSchema, "ARROW_TO_PAI_TYPE"):
        return getattr(SemanticLayerSchema, "ARROW_TO_PAI_TYPE")  # type: ignore[return-value]
    return _DEFAULT_ARROW_TO_PAI


def _pandas_to_pai_map() -> dict:
    if SemanticLayerSchema is not None and hasattr(SemanticLayerSchema, "PANDAS_TO_PAI_TYPE"):
        return getattr(SemanticLayerSchema, "PANDAS_TO_PAI_TYPE")  # type: ignore[return-value]
    # Derive minimal inverse map from Arrow fallback
    base = {
        "object": "string",
        "string": "string",
        "float64": "float",
        "float32": "float",
        "int64": "integer",
        "int32": "integer",
        "bool": "boolean",
    }
    return base

def map_pandas_dtype_to_pandasai(dtype: str) -> str:  # noqa: D401
    """Return the PandasAI column type for *dtype* using library or fallback."""
    return _pandas_to_pai_map().get(dtype.lower(), "string")

def get_csv_paths(csv_dir: str) -> List[str]:
    """Return a list of all CSV file paths in the given directory and subdirectories."""
    csv_paths = []
    for root, _, files in os.walk(csv_dir):
        for f in files:
            if f.endswith(".csv"):
                csv_paths.append(os.path.join(root, f))
    return csv_paths

def load_descriptions(desc_path: str) -> dict:
    if os.path.exists(desc_path):
        with open(desc_path, "r") as f:
            return yaml.safe_load(f) or {}
    return {}

def save_descriptions(desc_path: str, descriptions: dict):
    """Save descriptions dictionary to a YAML file."""
    with open(desc_path, "w") as f:
        yaml.dump(descriptions, f, sort_keys=False)

def generate_all_descriptions(csv_paths: list, desc_path: str) -> dict:
    descriptions = load_descriptions(desc_path)
    for path in csv_paths:
        dataset_name = os.path.splitext(os.path.basename(path))[0].replace(" ", "-").replace("_", "-").lower()
        if dataset_name not in descriptions:
            # Only call OpenAI if not already present
            # Use the previous OpenAI code here (or a stub if you want to remove OpenAI entirely)
            desc = f"Dataset loaded from {dataset_name}. (No OpenAI description generated.)"
            descriptions[dataset_name] = desc
    save_descriptions(desc_path, descriptions)
    return descriptions

# ---------------------------------------------------------------------------
# 🔑  Dynamic synonym mapping (PostgreSQL / Parquet) powered by FlashText
# ---------------------------------------------------------------------------

try:
    from flashtext import KeywordProcessor  # type: ignore
    FLASHTEXT_AVAILABLE = True
except ImportError:  # pragma: no cover - handled gracefully at runtime
    KeywordProcessor = None  # type: ignore
    FLASHTEXT_AVAILABLE = False

# ---------------------------------------------------------------------------
# Static-typing: import KeywordProcessor only for type-checking to avoid runtime
# dependency issues when *flashtext* is absent.  Using the fully-qualified
# name in forward references sidesteps mypy's "Variable not allowed in type
# expression" error that arises because we later redefine `KeywordProcessor`
# at runtime.
# ---------------------------------------------------------------------------

if TYPE_CHECKING:  # pragma: no cover - only evaluated by type checkers
    from flashtext import KeywordProcessor as FlashTextKeywordProcessor  # noqa: F401 - type alias only

def sanitize_column_names(df: pd.DataFrame) -> pd.DataFrame:
    """Sanitize DataFrame column names for PandasAI: only letters, numbers, and underscores."""
    df = df.copy()
    df.columns = [re.sub(r'[^A-Za-z0-9_]', '_', col) for col in df.columns]
    return df

# Ensure the top-level datasets directory exists at import time
DATASETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "datasets")
os.makedirs(DATASETS_DIR, exist_ok=True)

def get_datasets_dir():
    """Return the absolute path to the top-level datasets directory."""
    return DATASETS_DIR

def load_csvs_as_pandasai_datasets(csv_dir: str) -> Dict[str, dict]:
    """
    Load all CSVs found under *csv_dir* into PandasAI datasets.
    Column names are sanitized but no additional synonym harmonisation is performed.
    Returns a dictionary mapping dataset names to their PandasAI dataset objects.
    """
    import logging
    datasets = {}
    csv_paths = get_csv_paths(csv_dir)
    # Create a unique parent folder for this session
    unique_suffix = time.strftime("%Y-%m-%d-%H-%M-%S")
    parent_dir = os.path.join(get_datasets_dir(), f"datasets-{unique_suffix}")
    os.makedirs(parent_dir, exist_ok=True)
    logging.info(f"Created/using datasets parent directory: {parent_dir}")
    # Generate/load all descriptions once
    desc_path = os.path.join(parent_dir, "descriptions.yaml")
    descriptions = generate_all_descriptions(csv_paths, desc_path)
    # (Optional) load mapping tables is now skipped - datasets are loaded as-is
    for path in csv_paths:
        try:
            dataset_name = os.path.splitext(os.path.basename(path))[0].replace(" ", "-").replace("_", "-").lower()
            dataset_dir = os.path.join(parent_dir, dataset_name)
            parquet_path = os.path.join(dataset_dir, "data.parquet")
            schema_path = os.path.join(dataset_dir, "schema.yaml")
            # Skip if both files exist in the directory
            if os.path.exists(dataset_dir) and os.path.exists(parquet_path) and os.path.exists(schema_path):
                _logger.info("Skipping %s: data.parquet and schema.yaml already exist.", dataset_name)
                continue
            df = pd.read_csv(path, na_values=["NA"], low_memory=False)
            if df.empty:
                _logger.warning("%s is empty. Skipping.", path)
                continue
            # Sanitize column names for PandasAI compliance
            df = sanitize_column_names(df)
            # No synonym harmonisation - use the raw data as-is
            pai_df = pai.DataFrame(df)

            # 📝  Derive PandasAI column schema with preference for the
            # built-in semantic layer utilities (if available).
            columns = _infer_columns(df)

            if not columns:
                _logger.warning("%s has no valid columns. Skipping.", path)
                continue
            # Use stored description, add harmonization note
            description = descriptions.get(dataset_name, f"Dataset loaded from {dataset_name}. (No description found.)")
            # Save DataFrame as Parquet and schema as YAML in the correct subfolder
            os.makedirs(dataset_dir, exist_ok=True)
            logging.info(f"Created/using dataset directory: {dataset_dir}")
            df.to_parquet(parquet_path, index=False)
            schema = {
                "description": description,
                "columns": columns
            }
            with open(schema_path, "w") as f:
                yaml.dump(schema, f, sort_keys=False)
            # Extract organization from the CSV path (e.g., GDSC or CCLE)
            org = None
            parts = os.path.normpath(path).split(os.sep)
            for p in parts:
                if p.lower() in ["gdsc", "ccle"]:
                    org = p.lower()
                    break
            if org:
                pandasai_path = f"{org}/{dataset_name}"
            else:
                pandasai_path = dataset_name  # fallback
            dataset = pai.create(
                path=pandasai_path,
                df=pai_df,
                description=description,
                columns=columns
            )
            datasets[dataset_name] = dataset
        except Exception as e:
            _logger.error("Error loading %s: %s", path, e)
    return datasets

# -------------------------------------------------------------
# Helper to generate a fully-fledged PandasAI column specification
# list from a pandas DataFrame.  We preferentially rely on the
# built-in SemanticLayerSchema utilities (if present) to ensure we
# stay in sync with PandasAI's own type-mapping rules.  When the
# helper is not available we fall back to a manual implementation
# that mirrors older behaviour.
# -------------------------------------------------------------

def _infer_columns(df: pd.DataFrame) -> List[dict]:
    """Return a PandasAI-compatible column schema for *df*.

    This tries to delegate to `SemanticLayerSchema.from_dataframe` when
    available (PandasAI ≥3.x).  Any failures fall back to the legacy
    manual mapping present in earlier versions of this helper module.
    """

    if SemanticLayerSchema is not None and hasattr(SemanticLayerSchema, "from_dataframe"):
        try:
            return SemanticLayerSchema.from_dataframe(df)  # type: ignore[call-arg]
        except Exception:
            pass  # Fall back on manual mapping below if the call fails

    # Manual inference fallback
    return [
        {
            "name": col,
            "type": map_pandas_dtype_to_pandasai(str(df[col].dtype)),
            "description": f"Column '{col}'",
        }
        for col in df.columns
    ]

# =============================================================================
# 💡 PostgreSQL → PandasAI dataset helper (fast init)
# =============================================================================

def _get_remote_pg_connection():
    """Get remote PostgreSQL connection parameters using DBEAVER_* environment variables."""
    # Helper to parse DBEAVER_URL
    def _parse_dbeaver_url(url: str) -> dict:
        from urllib.parse import urlparse
        if not url:
            return {}
        if not url.startswith('postgresql://'):
            url = f'postgresql://{url}'
        parsed = urlparse(url)
        return {
            "host": parsed.hostname,
            "port": parsed.port or 5432
        }
    
    # Always use DBEAVER_* environment variables (remote Azure database only)
    dbeaver_url = os.getenv("DBEAVER_URL")
    if not dbeaver_url:
        raise RuntimeError("DBEAVER_URL environment variable is required for remote database connection")
    
    url_params = _parse_dbeaver_url(dbeaver_url)
    return {
        "host": url_params.get("host"),
        "port": int(url_params.get("port", 5432)),
        "user": os.getenv("DBEAVER_USERNAME"),
        "password": os.getenv("DBEAVER_PASSWORD"),
        "database": os.getenv("DBEAVER_DATABASE")
    }

def _pg_type_to_pai(pg_type: str) -> str:  # noqa: D401 - simple mapper
    """Return closest PandasAI type for a PostgreSQL data_type string."""
    primitive = pg_type.lower().split("(")[0]
    return {
        # strings
        "character varying": "string",
        "varchar": "string",
        "character": "string",
        "char": "string",
        "text": "string",
        # integers
        "integer": "integer",
        "int": "integer",
        "bigint": "integer",
        "smallint": "integer",
        # floats
        "double precision": "float",
        "real": "float",
        "numeric": "float",
        "decimal": "float",
        # bool/date/time
        "boolean": "boolean",
        "date": "datetime",
        "timestamp": "datetime",
        "timestamp without time zone": "datetime",
        "timestamp with time zone": "datetime",
        "time": "datetime",
        "time without time zone": "datetime",
    }.get(primitive, "string")


def _infer_pg_columns(cursor, table_name: str, schema: str = "public") -> list[dict]:
    """Introspect `information_schema.columns` to build PandasAI column schema."""
    cursor.execute(
        _pg_sql.SQL(
            """SELECT column_name, data_type
                 FROM information_schema.columns
                 WHERE table_schema = %s AND table_name = %s
                 ORDER BY ordinal_position;"""
        ),
        (schema, table_name),
    )
    return [
        {
            "name": col,
            "type": _pg_type_to_pai(dtype),
            "description": f"Column '{col}' from PostgreSQL table '{schema}.{table_name}'",
        }
        for col, dtype in cursor.fetchall()
    ]


def create_postgres_dataset(
    table_name: str,
    dataset_path: str,
    description: str | None = None,
    schema: str = "public",
    parquet_path: str | None = None,  # Kept for API compatibility, but ignored
) -> dict:
    """Connect to an existing remote PostgreSQL-backed PandasAI dataset. Table must already exist in the remote Azure database."""
    # Use remote PostgreSQL connection parameters
    conn_params = _get_remote_pg_connection()
    host = conn_params["host"]
    port = conn_params["port"]
    user = conn_params["user"]
    password = conn_params["password"]
    database = conn_params["database"]
    
    # Validate required connection parameters
    missing = []
    if not host: missing.append("DBEAVER_URL host")
    if not user: missing.append("DBEAVER_USERNAME")
    if not database: missing.append("DBEAVER_DATABASE")
    # Note: Password is optional for some authentication methods
    if missing:
        raise RuntimeError(f"Missing required remote PostgreSQL environment variables: {', '.join(missing)}")
    
    # ------------------------------------------------------------------
    # 🎯 Remote PostgreSQL ONLY - Table must already exist
    # ------------------------------------------------------------------

    # Debug: Log connection parameters (but mask password)
    _logger.info(f"Connecting to remote PostgreSQL with host={host}, port={port}, user={user}, database={database}, schema={schema}, table_name={table_name}")
    if password:
        _logger.debug("Password is provided")
    else:
        _logger.debug("No password provided - attempting connection without password")

    # ------------------------------------------------------------------
    # 🎯 Smart dataset path resolution with timestamping for conflicts
    # ------------------------------------------------------------------

    clear_existing = os.getenv("CLEAR_EXISTING_DATASET", "false").lower() == "true"
    if clear_existing:
        _logger.info(f"CLEAR_EXISTING_DATASET=true, attempting to remove: {dataset_path}")
        if reset_dataset_if_exists(dataset_path):
            _logger.info(f"Successfully cleared existing dataset: {dataset_path}")
        final_dataset_path = dataset_path
        copied_schema = None
    else:
        final_dataset_path, copied_schema = get_or_create_unique_dataset_path(dataset_path)
        if final_dataset_path == dataset_path:
            try:
                existing_ds = pai.load(dataset_path)  # changed from get to load
                if existing_ds is not None:
                    _logger.info(f"Found existing dataset at path: {dataset_path}")
                    return {"dataset": existing_ds, "columns": getattr(existing_ds, "_columns", []), "table_name": table_name, "db": database}
            except Exception as e:
                _logger.debug(f"Final check for existing dataset failed: {e}")

    _logger.info(f"Using dataset path: {final_dataset_path}")
    dataset_path = final_dataset_path

    # Single-attempt connection - no fallbacks, no Docker auto-start.
    try:
        conn = psycopg2.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            dbname=database,
        )
    except psycopg2.OperationalError as exc:
        _logger.error(
            "Failed to connect to PostgreSQL at %s:%s with user '%s'. Ensure the server is running and credentials are correct. (%s)",
            host,
            port,
            user,
            exc,
        )
        raise

    cur = conn.cursor()
    # ------------------------------------------------------------------
    # Debug: Print all tables in the schema before checking for the table
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = %s", (schema,))
    tables_in_schema = [row[0] for row in cur.fetchall()]
    _logger.info(f"Tables in schema '{schema}': {tables_in_schema}")
    # ------------------------------------------------------------------
    # Check if the target table exists
    cur.execute(
        """SELECT 1 FROM information_schema.tables WHERE table_schema = %s AND table_name = %s""",
        (schema, table_name),
    )
    exists = cur.fetchone() is not None

    if not exists:
        cur.close()
        conn.close()
        raise RuntimeError(
            f"PostgreSQL table '{schema}.{table_name}' does not exist. "
            f"The required table must already exist in the remote Azure database. "
            f"Automatic import or creation is not supported."
        )

    # Table already existed - fetch column info directly
    cur.execute(
        """SELECT column_name, data_type FROM information_schema.columns
           WHERE table_schema = %s AND table_name = %s
           ORDER BY ordinal_position;""",
        (schema, table_name),
    )
    columns = [
        {
            "name": col,
            "type": _pg_type_to_pai(dtype),
            "description": f"Column '{col}' from PostgreSQL table '{schema}.{table_name}'",
        }
        for col, dtype in cur.fetchall()
    ]

    cur.close()
    conn.close()

    # Prepare dataset description and schema (use copied schema if available)
    if copied_schema and copied_schema.get("description"):
        description = copied_schema["description"]
        _logger.info("Using description from existing dataset schema")
    elif not description:
        description = f"PostgreSQL table '{schema}.{table_name}' exposed via PandasAI semantic layer (db: {database})."

    if copied_schema and copied_schema.get("columns"):
        final_columns = copied_schema["columns"]
        _logger.info(f"Using {len(final_columns)} columns from existing dataset schema")
    else:
        final_columns = columns
        _logger.info(f"Using {len(final_columns)} newly inferred columns")

    try:
        # Build connection parameters for PandasAI
        connection_params = {
            "host": host,
            "port": port,
            "user": user,
            "database": database,
        }
        
        # Only include password if it's not empty - some PandasAI versions 
        # may require password to be present, others may not accept empty strings
        if password:
            connection_params["password"] = password
        else:
            # Try with empty password for remote PostgreSQL authentication
            connection_params["password"] = ""
        
        create_params = {
            "path": dataset_path,
            "description": description,
            "source": {
                "type": "postgres",
                "connection": connection_params,
                "table": table_name,
                "schema": schema,
                "columns": final_columns,
            },
        }
        if copied_schema and copied_schema.get("metadata"):
            create_params["metadata"] = copied_schema["metadata"]
        dataset = pai.create(**create_params)
        if copied_schema:
            _logger.info(f"Successfully created timestamped dataset: {dataset_path} (with preserved schema)")
        else:
            _logger.info(f"Successfully created new dataset: {dataset_path}")

        # --- Write YAML schema for PostgreSQL dataset ---
        import yaml
        # Determine schema file path under datasets/ matching dataset_path
        datasets_dir = get_datasets_dir()
        # dataset_path may be like "postgres/my_dataset" or just "my_dataset"
        schema_dir = os.path.join(datasets_dir, *dataset_path.split("/"))
        os.makedirs(schema_dir, exist_ok=True)
        schema_path = os.path.join(schema_dir, "schema.yaml")
        schema_yaml = {
            "description": description,
            "columns": final_columns,
            "table": table_name,
            "schema": schema,
            "database": database,
            "host": host,
            "port": port,
            # "user": user,  # Optionally include user, but not password
        }
        with open(schema_path, "w") as f:
            yaml.dump(schema_yaml, f, sort_keys=False)
        _logger.info(f"Wrote PostgreSQL dataset schema to {schema_path}")
        # --- End YAML schema write ---

    except Exception as create_error:
        _logger.error(f"Failed to create dataset {dataset_path}: {create_error}")
        if "already exists" in str(create_error).lower() or "duplicate" in str(create_error).lower():
            _logger.info("Dataset already exists, attempting to retrieve it...")
            try:
                dataset = pai.load(dataset_path)  # changed from get to load
                if dataset is not None:
                    _logger.info(f"Successfully retrieved existing dataset: {dataset_path}")
                    return {"dataset": dataset, "columns": final_columns, "table_name": table_name, "db": database}
            except Exception as get_error:
                _logger.error(f"Could not retrieve existing dataset: {get_error}")
            error_msg = (
                f"Dataset '{dataset_path}' already exists but cannot be retrieved. "
                f"Try setting environment variable CLEAR_EXISTING_DATASET=true to force recreation, "
                f"or restart your application to clear the PandasAI registry."
            )
            raise RuntimeError(error_msg) from create_error
        else:
            raise create_error

    return {"dataset": dataset, "columns": final_columns, "table_name": table_name, "db": database}

# ---------------------------------------------------------------------------
# 🔍  Synonym source helpers (PostgreSQL / Parquet)
# ---------------------------------------------------------------------------

# REVISED: Fetch mappings between original names and canonical IDs from PostgreSQL.
def _fetch_cell_line_mappings_postgres(
    *,
    table_name: str,
    schema: str = "public",
    limit: int | None = None,
) -> list[tuple[str, str]]:
    """Fetch distinct mappings of (original_cell_name, col_name) from PostgreSQL."""
    conn_params = _get_remote_pg_connection()
    host = conn_params["host"]
    port = conn_params["port"]
    user = conn_params["user"]
    password = conn_params["password"]
    database = conn_params["database"]
    
    # Validate required connection parameters
    missing = []
    if not host: missing.append("DBEAVER_URL host")
    if not user: missing.append("DBEAVER_USERNAME")
    if not database: missing.append("DBEAVER_DATABASE")
    if missing:
        raise RuntimeError(f"Missing required PostgreSQL environment variables: {', '.join(missing)}")
        
    with psycopg2.connect(host=host, port=port, user=user, password=password, dbname=database) as conn:
        with conn.cursor() as cur:
            query = (
                f"SELECT DISTINCT original_cell_name, col_name FROM {schema}.{table_name} "
                f"WHERE original_cell_name IS NOT NULL AND col_name IS NOT NULL AND col_name <> ''"
            )
            if limit is not None:
                query += f" LIMIT {int(limit)}"
            cur.execute(query)
            return [(row[0], row[1]) for row in cur.fetchall() if row and row[0] and row[1]]

# REVISED: Fetch mappings between original drug names and canonical IDs from PostgreSQL.
def _fetch_drug_mappings_postgres(
    *,
    table_name: str,
    schema: str = "public",
    limit: int | None = None,
) -> list[tuple[str, str]]:
    """Fetch distinct mappings of (original_row_name, row_name) for drug data from PostgreSQL."""
    conn_params = _get_remote_pg_connection()
    host = conn_params["host"]
    port = conn_params["port"]
    user = conn_params["user"]
    password = conn_params["password"]
    database = conn_params["database"]
    
    # Validate required connection parameters
    missing = []
    if not host: missing.append("DBEAVER_URL host")
    if not user: missing.append("DBEAVER_USERNAME")
    if not database: missing.append("DBEAVER_DATABASE")
    if missing:
        raise RuntimeError(f"Missing required PostgreSQL environment variables: {', '.join(missing)}")
        
    with psycopg2.connect(host=host, port=port, user=user, password=password, dbname=database) as conn:
        with conn.cursor() as cur:
            query = (
                f"SELECT DISTINCT original_row_name, row_name FROM {schema}.{table_name} "
                f"WHERE data_type = 'act' AND original_row_name IS NOT NULL AND row_name IS NOT NULL AND row_name <> ''"
            )
            if limit is not None:
                query += f" LIMIT {int(limit)}"
            cur.execute(query)
            return [(row[0], row[1]) for row in cur.fetchall() if row and row[0] and row[1]]

# REVISED: Build KeywordProcessor from (synonym, canonical_id) pairs.
def _build_kp_from_canonical_mappings(mappings: list[tuple[str, str]]) -> "FlashTextKeywordProcessor":
    """Return a FlashText *KeywordProcessor* built from canonical ID mappings.
    
    Each element in *mappings* is expected to be a tuple of (original_name, canonical_id).
    The original names become the keywords that get replaced with canonical IDs.
    """
    if not FLASHTEXT_AVAILABLE:
        raise RuntimeError("flashtext is not installed - install it or disable synonym harmonisation.")

    kp: "FlashTextKeywordProcessor" = KeywordProcessor(case_sensitive=False)  # type: ignore[call-arg]

    for original_name, canonical_id in mappings:
        if not original_name or not canonical_id or not isinstance(original_name, str) or not isinstance(canonical_id, str):
            continue
        # Map the original name (synonym) to the canonical ID
        kp.add_keyword(original_name.strip(), canonical_id.strip())

    return kp

# ---------------------------------------------------------------------------
# 🔧  Public builders
# ---------------------------------------------------------------------------

def build_cell_line_keyword_processor(
    *,
    source: str = "postgres",
    postgres_table: str = "cellminer_data_new",
    postgres_schema: str = "public",
) -> "FlashTextKeywordProcessor":
    """Return a KeywordProcessor for cell-line synonyms using the chosen *source*.
    
    This function now creates a mapping from the original dataset-specific cell line names
    to their canonical Cellosaurus IDs by querying the corrected database schema.
    """
    if source == "postgres":
        mappings = _fetch_cell_line_mappings_postgres(
            table_name=postgres_table,
            schema=postgres_schema,
        )
    else:
        raise ValueError("source must be 'postgres'. Parquet source is deprecated.")

    return _build_kp_from_canonical_mappings(mappings)


def build_drug_keyword_processor(
    *,
    source: str = "postgres",
    postgres_table: str = "cellminer_data_new",
    postgres_schema: str = "public",
) -> "FlashTextKeywordProcessor":
    """Return a KeywordProcessor for drug synonyms using the chosen *source*.
    
    This function now creates a mapping from the original dataset-specific drug names
    to their canonical PubChem CIDs by querying the corrected database schema.
    """
    if source == "postgres":
        mappings = _fetch_drug_mappings_postgres(
            table_name=postgres_table,
            schema=postgres_schema,
        )
    else:
        raise ValueError("source must be 'postgres'. Parquet source is deprecated.")

    return _build_kp_from_canonical_mappings(mappings)


# ---------------------------------------------------------------------------
# 🎯  Harmonisation entry points
# ---------------------------------------------------------------------------

def harmonize_cell_line_names(
    df: pd.DataFrame,
    *,
    kp: "FlashTextKeywordProcessor" | None = None,
    source: str = "postgres",
    table_name: str = "cellminer_data_new",
    schema: str = "public",
) -> pd.DataFrame:
    """Return *df* with cell-line names canonicalised via FlashText."""
    if not FLASHTEXT_AVAILABLE:
        return df

    if kp is None:
        kp = build_cell_line_keyword_processor(
            source=source,
            postgres_table=table_name,
            postgres_schema=schema,
        )

    df = df.copy()

    # Prefer the explicit original source column introduced by the new schema.
    if "original_cell_name" in df.columns:
        # Use original_cell_name to derive/overwrite the canonical `col_name`.
        try:
            df["col_name"] = df["original_cell_name"].astype(str).apply(kp.replace_keywords)  # type: ignore[attr-defined]
        except Exception:
            # Fallback to naive replacement on the original column itself
            df["original_cell_name"] = df["original_cell_name"].astype(str).apply(kp.replace_keywords)  # type: ignore[attr-defined]
        return df

    # Backwards-compatible fallback: look for common column names
    candidate_cols = [c for c in df.columns if c.lower() in {"cell_line", "cellline", "cell line", "col_name"}]
    if not candidate_cols:
        return df

    col = candidate_cols[0]
    df[col] = df[col].astype(str).apply(kp.replace_keywords)  # type: ignore[attr-defined]
    return df


def harmonize_drug_names(
    df: pd.DataFrame,
    *,
    kp: "FlashTextKeywordProcessor" | None = None,
    source: str = "postgres",
    table_name: str = "cellminer_data_new",
    schema: str = "public",
) -> pd.DataFrame:
    """Return *df* with drug names canonicalised via FlashText."""
    if not FLASHTEXT_AVAILABLE:
        return df

    if kp is None:
        kp = build_drug_keyword_processor(
            source=source,
            postgres_table=table_name,
            postgres_schema=schema,
        )

    df = df.copy()

    # Prefer the explicit original source column introduced by the new schema.
    if "original_row_name" in df.columns:
        # Use original_row_name to derive/overwrite the canonical `row_name`.
        try:
            df["row_name"] = df["original_row_name"].astype(str).apply(kp.replace_keywords)  # type: ignore[attr-defined]
        except Exception:
            df["original_row_name"] = df["original_row_name"].astype(str).apply(kp.replace_keywords)  # type: ignore[attr-defined]
        return df

    # Backwards-compatible fallback: look for common column names
    candidate_cols = [c for c in df.columns if c.lower() in {"drug", "drug_name", "row_name", "compound"}]
    if not candidate_cols:
        return df

    col = candidate_cols[0]
    df[col] = df[col].astype(str).apply(kp.replace_keywords)  # type: ignore[attr-defined]
    return df

def clear_pandasai_dataset_registry():
    """Clear all PandasAI dataset registries (useful for debugging/resetting)."""
    try:
        # Try different methods to clear the registry
        cleared_count = 0
        
        # Method 1: If there's a clear_all method
        if hasattr(pai, 'clear_all'):
            pai.clear_all()  # type: ignore[attr-defined]
            _logger.info("Cleared all datasets using pai.clear_all()")
            return True
            
        # Method 2: List and delete individual datasets
        if hasattr(pai, 'list_datasets'):
            try:
                datasets = pai.list_datasets()  # type: ignore[attr-defined]
                for dataset_path in datasets:
                    try:
                        if hasattr(pai, 'delete'):
                            pai.delete(dataset_path)  # type: ignore[attr-defined]
                        elif hasattr(pai, 'remove'):
                            pai.remove(dataset_path)  # type: ignore[attr-defined]
                        cleared_count += 1
                    except Exception as e:
                        _logger.warning(f"Could not delete dataset {dataset_path}: {e}")
                
                _logger.info(f"Cleared {cleared_count} datasets from registry")
                return cleared_count > 0
            except Exception as e:
                _logger.error(f"Could not list datasets: {e}")
        
        # Method 3: Try to clear internal registry if accessible
        try:
            if hasattr(pai, '_registry'):
                pai._registry.clear()  # type: ignore[attr-defined]
                _logger.info("Cleared internal registry")
                return True
        except Exception as e:
            _logger.debug(f"Could not access internal registry: {e}")
        
        return False
        
    except Exception as e:
        _logger.error(f"Failed to clear dataset registry: {e}")
        return False

def reset_dataset_if_exists(dataset_path: str) -> bool:
    """Try to remove a specific dataset from PandasAI registry."""
    try:
        # Check if dataset exists and try to remove it
        if hasattr(pai, 'delete'):
            pai.delete(dataset_path)  # type: ignore[attr-defined]
            _logger.info(f"Successfully deleted dataset: {dataset_path}")
            return True
        elif hasattr(pai, 'remove'):
            pai.remove(dataset_path)  # type: ignore[attr-defined]
            _logger.info(f"Successfully removed dataset: {dataset_path}")
            return True
        else:
            _logger.warning("No delete/remove method available")
            return False
    except Exception as e:
        _logger.debug(f"Could not remove dataset {dataset_path}: {e}")
        return False

def generate_timestamped_dataset_path(original_path: str) -> str:
    """Generate a new dataset path with timestamp suffix."""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    
    # Handle paths like "org/dataset" or just "dataset"
    if "/" in original_path:
        org, dataset_name = original_path.rsplit("/", 1)
        return f"{org}/{dataset_name}-{timestamp}"
    else:
        return f"{original_path}-{timestamp}"

def copy_dataset_schema(source_dataset, target_path: str) -> dict:
    """Copy schema information from an existing dataset for a new timestamped dataset."""
    try:
        schema_info = {
            "description": getattr(source_dataset, "description", ""),
            "columns": getattr(source_dataset, "_columns", [])
        }
        
        # If the source has additional metadata, try to preserve it
        if hasattr(source_dataset, "_metadata"):
            schema_info["metadata"] = getattr(source_dataset, "_metadata", {})
            
        # Add timestamp information to description
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        original_desc = schema_info["description"]
        schema_info["description"] = (
            f"{original_desc}\n\nTimestamped copy created at {timestamp} "
            f"due to existing dataset conflict."
        )
        
        _logger.info(f"Copied schema from existing dataset to {target_path}")
        return schema_info
        
    except Exception as e:
        _logger.warning(f"Could not copy schema from existing dataset: {e}")
        return {}

def get_or_create_unique_dataset_path(requested_path: str, max_attempts: int = 5) -> tuple[str, dict | None]:
    """
    Get a unique dataset path, creating timestamped versions if conflicts exist.
    Returns (final_path, existing_schema_if_copied)
    """
    original_schema = None
    
    # First, try the original path
    try:
        existing_ds = pai.load(requested_path)  # type: ignore[attr-defined]
        if existing_ds is not None:
            _logger.info(f"Dataset exists at {requested_path}, creating timestamped version")
            original_schema = copy_dataset_schema(existing_ds, requested_path)
            
            # Generate timestamped alternatives
            for attempt in range(max_attempts):
                timestamped_path = generate_timestamped_dataset_path(requested_path)
                try:
                    test_ds = pai.load(timestamped_path)  # type: ignore[attr-defined]
                    if test_ds is None:
                        _logger.info(f"Using unique path: {timestamped_path}")
                        return timestamped_path, original_schema
                except Exception:
                    # Path doesn't exist, we can use it
                    _logger.info(f"Using unique path: {timestamped_path}")
                    return timestamped_path, original_schema
                    
                # If we get here, even the timestamped path exists, try again
                time.sleep(1)  # Wait 1 second to ensure different timestamp
                
            # If all attempts failed, use the last generated path anyway
            final_path = generate_timestamped_dataset_path(requested_path)
            _logger.warning(f"Max attempts reached, using: {final_path}")
            return final_path, original_schema
            
    except Exception as e:
        _logger.debug(f"No existing dataset found at {requested_path}: {e}")
    
    # Original path is available
    return requested_path, None

def list_all_datasets() -> dict:
    """List all available PandasAI datasets with their creation info."""
    try:
        datasets_info = {}
        
        # Try to get all datasets
        if hasattr(pai, 'list_datasets'):
            datasets = pai.list_datasets()  # type: ignore[attr-defined]
            
            for path, dataset in datasets.items():
                info = {
                    "path": path,
                    "description": getattr(dataset, "description", "No description"),
                    "columns_count": len(getattr(dataset, "_columns", [])),
                    "is_timestamped": bool(re.search(r'-\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}$', path))
                }
                
                # Extract timestamp if it's a timestamped dataset
                timestamp_match = re.search(r'-(\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2})$', path)
                if timestamp_match:
                    timestamp_str = timestamp_match.group(1)
                    try:
                        # Convert to readable format
                        timestamp_dt = datetime.datetime.strptime(timestamp_str, "%Y-%m-%d-%H-%M-%S")
                        info["created_at"] = timestamp_dt.strftime("%Y-%m-%d %H:%M:%S")
                        info["original_path"] = path.replace(f"-{timestamp_str}", "")
                    except ValueError:
                        info["created_at"] = "Unknown"
                        info["original_path"] = "Unknown"
                else:
                    info["created_at"] = "Original dataset"
                    info["original_path"] = path
                
                datasets_info[path] = info
        
        return datasets_info
        
    except Exception as e:
        _logger.error(f"Could not list datasets: {e}")
        return {}

def cleanup_old_timestamped_datasets(base_path: str, keep_latest: int = 3) -> int:
    """Clean up old timestamped datasets, keeping only the latest N versions."""
    try:
        all_datasets = list_all_datasets()
        
        # Group datasets by base path
        timestamped_datasets = []
        for path, info in all_datasets.items():
            if info["is_timestamped"] and info["original_path"] == base_path:
                timestamped_datasets.append((path, info))
        
        # Sort by creation time (newest first)
        timestamped_datasets.sort(key=lambda x: x[1]["created_at"], reverse=True)
        
        # Remove old datasets (keep only the latest N)
        removed_count = 0
        for path, info in timestamped_datasets[keep_latest:]:
            try:
                if reset_dataset_if_exists(path):
                    _logger.info(f"Cleaned up old timestamped dataset: {path}")
                    removed_count += 1
            except Exception as e:
                _logger.warning(f"Could not remove old dataset {path}: {e}")
        
        return removed_count
        
    except Exception as e:
        _logger.error(f"Error during cleanup: {e}")
        return 0
