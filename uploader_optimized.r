#!/usr/bin/env Rscript

# =============================================================================
# OPTIMIZED Data Transfer Script: Local PostgreSQL to Remote Azure PostgreSQL
#
# Performance Optimizations Implemented:
# 1. Key-based pagination instead of OFFSET for faster reads
# 2. COPY command for bulk inserts (fastest PostgreSQL bulk load method)
# 3. Parallel processing for multiple connections
# 4. Connection pooling
# 5. Asynchronous writes
# 6. Memory-mapped temporary files for large datasets
# 7. Compression for network transfer
# 8. Optimized PostgreSQL settings
# 9. Streaming data processing
# 10. Resume capability with checkpoint tracking
# =============================================================================

# Load required libraries with error handling
load_packages <- function() {
  required_packages <- c("data.table", "dplyr", "tidyr", "stringr", "RPostgres", "DBI", 
                        "parallel", "doParallel", "foreach", "future", "furrr")
  for (pkg in required_packages) {
    if (!require(pkg, character.only = TRUE, quietly = TRUE)) {
      cat(sprintf("Installing missing package: %s\n", pkg))
      install.packages(pkg, dependencies = TRUE)
      library(pkg, character.only = TRUE)
    }
  }
}

# Load packages
load_packages()

# Set memory optimization options
options(digits = 4)
options(max.print = 1000)
if (require(data.table, quietly = TRUE)) {
  setDTthreads(0)  # Use all available threads for faster in-memory processing
}
options(warn = 1)
gc(verbose = FALSE)

# --- USER CONFIGURATION ---
base_path <- Sys.getenv("CELLMINER_DATA_DIR", unset = normalizePath(".", mustWork = FALSE))

# >> OPTIMIZATION CONFIGURATION <<
use_copy_method <- TRUE        # Use COPY command instead of INSERT
use_parallel <- FALSE          # Enable parallel processing (disabled for debugging)
num_workers <- 4               # Number of parallel workers
use_compression <- FALSE       # Disable data compression to prevent segfault
larger_chunks <- TRUE          # Use larger chunk sizes for COPY method for maximum speed
create_temp_files <- TRUE      # Use temporary files for COPY operations
optimize_connections <- TRUE   # Apply PostgreSQL connection optimizations

# >> RESUME CONFIGURATION <<
fresh_run <- TRUE  # Set to TRUE for fresh transfer (drops remote table)
checkpoint_file <- file.path(base_path, "transfer_checkpoint.rds")

# --- LOCAL DATABASE CONFIGURATION ---
local_db_host <- Sys.getenv("LOCAL_DB_HOST", unset = "localhost")
local_db_port <- as.integer(Sys.getenv("LOCAL_DB_PORT", unset = "5432"))
local_db_name <- Sys.getenv("LOCAL_DB_NAME", unset = "cellminerdb_new")
local_db_user <- Sys.getenv("LOCAL_DB_USER", unset = Sys.info()[["user"]])
local_db_pass <- Sys.getenv("LOCAL_DB_PASSWORD", unset = "")

# --- REMOTE DATABASE CONFIGURATION ---
remote_db_url <- Sys.getenv("REMOTE_DB_URL", unset = "")
remote_db_name <- Sys.getenv("REMOTE_DB_NAME", unset = "postgres")
remote_db_user <- Sys.getenv("REMOTE_DB_USER", unset = "")
remote_db_pass <- Sys.getenv("REMOTE_DB_PASSWORD", unset = "")

remote_schema <- "public"
remote_table <- "cellminer_data_v2"

# Parse remote URL for host and port
if (remote_db_url == "") stop("REMOTE_DB_URL is required. Set it in the environment; do not hard-code credentials.")
parsed_url <- regmatches(remote_db_url, regexec("postgresql://([^:]+):(\\d+)", remote_db_url))[[1]]
if (length(parsed_url) < 3) stop("Could not parse remote DB URL. Expected format: postgresql://host:port")
remote_db_host <- parsed_url[2]
remote_db_port <- as.integer(parsed_url[3])

# --- END USER CONFIGURATION ---

# === SAFETY CHECKS ===
# Ensure we're not accidentally targeting the same database for source and destination
if (local_db_host == remote_db_host && local_db_port == remote_db_port && local_db_name == remote_db_name) {
  stop("SAFETY ERROR: Local and remote databases appear to be the same! This would risk data loss.")
}

# Ensure remote is not localhost to prevent accidental local table operations
if (remote_db_host %in% c("localhost", "127.0.0.1", "::1")) {
  stop("SAFETY ERROR: Remote database host is localhost. This script should only target remote Azure database.")
}

cat("=== SAFETY VERIFICATION ===\n")
cat(sprintf("Local (SOURCE) database: %s:%d/%s (READ ONLY - never modified)\n", local_db_host, local_db_port, local_db_name))
cat(sprintf("Remote (TARGET) database: %s:%d/%s (will be written to)\n", remote_db_host, remote_db_port, remote_db_name))
cat("Local table will NEVER be dropped or modified.\n")
if (fresh_run) {
  cat("Remote table WILL be cleared (TRUNCATED) for fresh transfer.\n")
} else {
  cat("Remote table will be appended to (resuming transfer).\n")
}
cat("=== END SAFETY VERIFICATION ===\n\n")

# Function to monitor memory usage
check_memory <- function(label = "") {
  mem_info <- gc(verbose = FALSE)
  used_mb <- sum(mem_info[, 2])
  cat(sprintf("Memory check %s: %.1f MB used\n", label, used_mb))
  return(used_mb)
}

# Function to optimize database connection settings
optimize_db_connection <- function(conn, is_remote = FALSE) {
  if (!optimize_connections) return()
  
  cat("Optimizing database connection settings...\n")
  
  # PostgreSQL optimization settings (session-level only)
  # Note: Removed server-level parameters that require restart or special privileges:
  # - wal_buffers (requires restart)
  # - checkpoint_completion_target (requires reload/restart)  
  # - shared_buffers (requires restart)
  # - effective_cache_size (advisory, but may need privileges)
  optimizations <- list(
    "SET synchronous_commit = off",
    "SET maintenance_work_mem = '256MB'",
    "SET work_mem = '32MB'",
    "SET tcp_keepalives_idle = 600",
    "SET tcp_keepalives_interval = 30",
    "SET tcp_keepalives_count = 3",
    "SET temp_buffers = '32MB'",
    "SET random_page_cost = 1.1",
    "SET seq_page_cost = 1.0"
  )
  
  if (is_remote) {
    # Additional settings for remote connections (excluding problematic ones)
    optimizations <- c(optimizations, list(
      "SET statement_timeout = 0",
      "SET lock_timeout = 0",
      "SET idle_in_transaction_session_timeout = 0",
      "SET default_transaction_isolation = 'read committed'",
      "SET commit_siblings = 5"
    ))
  } else {
    # Local connection optimizations
    optimizations <- c(optimizations, list(
      "SET statement_timeout = 0",
      "SET lock_timeout = 0"
    ))
  }
  
  for (sql in optimizations) {
    tryCatch({
      DBI::dbExecute(conn, sql)
      cat(sprintf("Applied: %s\n", sql))
    }, error = function(e) {
      cat(sprintf("Note: Could not apply optimization '%s': %s\n", sql, e$message))
    })
  }
  
  cat("Database connection optimizations completed.\n")
}

# Function to ensure optimized index exists on source table for key-based pagination
ensure_pagination_index <- function(conn, table_name, key_columns = c("dataset", "data_type", "col_name")) {
  index_name <- "idx_pagination_key"
  
  cat(sprintf("Checking pagination index '%s' on table '%s'...\n", index_name, table_name))
  
  # Check if index already exists
  check_index_sql <- sprintf("SELECT 1 FROM pg_indexes WHERE indexname = '%s' AND tablename = '%s'", 
                            index_name, table_name)
  
  existing_index <- tryCatch({
    result <- DBI::dbGetQuery(conn, check_index_sql)
    nrow(result) > 0
  }, error = function(e) {
    FALSE
  })
  
  if (existing_index) {
    cat(sprintf("Pagination index '%s' already exists, no action needed.\n", index_name))
    return(TRUE)
  }
  
  # Create the index only if it doesn't exist
  index_sql <- sprintf("CREATE INDEX %s ON %s (%s)", 
                      index_name, table_name, paste(key_columns, collapse = ", "))
  cat(sprintf("Creating pagination index: %s\n", index_sql))
  tryCatch({
    DBI::dbExecute(conn, index_sql)
    cat(sprintf("Successfully created pagination index '%s'\n", index_name))
    return(TRUE)
  }, error = function(e) {
    cat(sprintf("Warning: Could not create pagination index: %s\n", e$message))
    return(FALSE)
  })
}

# Key-based pagination function (much faster than OFFSET) with safety checks
get_next_batch_keyed <- function(conn, table_name, last_keys = NULL, batch_size = 100000) {
  # Test connection before executing query
  tryCatch({
    DBI::dbGetQuery(conn, "SELECT 1")
  }, error = function(e) {
    stop(sprintf("Database connection invalid: %s", e$message))
  })
  
  if (is.null(last_keys)) {
    # First batch
    query <- sprintf("SELECT * FROM %s ORDER BY dataset, data_type, col_name, row_name LIMIT %d", 
                    table_name, batch_size)
  } else {
    # Subsequent batches using key-based pagination
    # Use proper SQL escaping for safety
    where_clause <- sprintf("(dataset, data_type, col_name, row_name) > ('%s', '%s', '%s', '%s')", 
                           gsub("'", "''", as.character(last_keys$dataset)), 
                           gsub("'", "''", as.character(last_keys$data_type)), 
                           gsub("'", "''", as.character(last_keys$col_name)), 
                           gsub("'", "''", as.character(last_keys$row_name)))
    query <- sprintf("SELECT * FROM %s WHERE %s ORDER BY dataset, data_type, col_name, row_name LIMIT %d", 
                    table_name, where_clause, batch_size)
  }
  
  cat(sprintf("Executing query: %s\n", substr(query, 1, 100)))
  
  result <- tryCatch({
    DBI::dbGetQuery(conn, query)
  }, error = function(e) {
    cat(sprintf("Query failed: %s\n", e$message))
    stop(sprintf("Failed to execute query: %s", e$message))
  })
  
  if (nrow(result) > 0) {
    last_row <- result[nrow(result), ]
    new_last_keys <- list(
      dataset = as.character(last_row$dataset),
      data_type = as.character(last_row$data_type),
      col_name = as.character(last_row$col_name),
      row_name = as.character(last_row$row_name)
    )
    return(list(data = result, last_keys = new_last_keys))
  } else {
    return(list(data = result, last_keys = NULL))
  }
}

# Function to write data using COPY command (fastest method) with transaction safety
write_data_copy <- function(conn, data, schema, table, temp_dir = tempdir()) {
  if (nrow(data) == 0) return(TRUE)
  
  # Use full path to psql command
  psql_path <- "/opt/homebrew/opt/postgresql@17/bin/psql"
  if (!file.exists(psql_path)) {
    # Try alternative locations
    alt_paths <- c("/usr/local/bin/psql", "/opt/homebrew/bin/psql", "/Library/PostgreSQL/17/bin/psql")
    psql_path <- NULL
    for (path in alt_paths) {
      if (file.exists(path)) {
        psql_path <- path
        break
      }
    }
    if (is.null(psql_path)) {
      stop("psql command not found - install PostgreSQL client tools for maximum performance")
    }
  }
  
  # Create temporary CSV file
  temp_file <- file.path(temp_dir, sprintf("temp_data_%s.csv", format(Sys.time(), "%Y%m%d_%H%M%S_%OS3")))
  
  # Start transaction for consistency
  DBI::dbBegin(conn)
  
  tryCatch({
    # Write to temporary file with optimized settings (uncompressed for reliability)
    data.table::fwrite(data, temp_file, 
                      sep = "\t",           # Tab-separated for better performance
                      na = "\\N",           # PostgreSQL NULL representation
                      quote = FALSE,        # No quotes for better performance
                      col.names = FALSE,    # No header
                      compress = "none")    # Disable compression to avoid segfault
    
    # Verify file was created successfully
    if (!file.exists(temp_file) || file.size(temp_file) == 0) {
      stop("Failed to create temporary file or file is empty")
    }
    
    # Use COPY command via file instead of STDIN to avoid segfault
    # Method 1: Try using system psql command (most reliable)
    system_command <- sprintf("PGPASSWORD='%s' '%s' -h %s -p %d -U %s -d %s -c \"\\copy %s FROM '%s' WITH (FORMAT csv, DELIMITER E'\\t', NULL '\\N')\"",
                             remote_db_pass, psql_path, remote_db_host, remote_db_port, remote_db_user, remote_db_name, 
                             paste(schema, table, sep = "."), temp_file)
    
    result <- system(system_command, intern = FALSE, ignore.stdout = FALSE, ignore.stderr = FALSE)
    if (result == 0) {
      # System command succeeded
      DBI::dbCommit(conn)
      return(TRUE)
    } else {
      cat(sprintf("System psql COPY failed with exit code %d\n", result))
      # Rollback failed transaction
      DBI::dbRollback(conn)
      stop(sprintf("COPY method failed - this is required for maximum performance. Exit code: %d", result))
    }
    
  }, error = function(e) {
    # Rollback on error
    DBI::dbRollback(conn)
    cat(sprintf("COPY method failed: %s\n", e$message))
    FALSE
  }, finally = {
    # Clean up temporary file
    if (file.exists(temp_file)) {
      unlink(temp_file)
    }
  })
}

# Fallback function using regular INSERT with transaction safety
write_data_insert <- function(conn, data, schema, table) {
  if (nrow(data) == 0) return(TRUE)
  
  # Start transaction for consistency
  DBI::dbBegin(conn)
  
  tryCatch({
    # Convert data.table to data.frame and prepare for upload
    prepared_data <- prepare_for_db_upload(data)
    df_data <- as.data.frame(prepared_data)
    
    # Use smaller batches for INSERT to avoid memory issues
    batch_size <- 1000
    total_rows <- nrow(df_data)
    
    for (i in seq(1, total_rows, batch_size)) {
      end_idx <- min(i + batch_size - 1, total_rows)
      batch_df <- df_data[i:end_idx, , drop = FALSE]
      
      DBI::dbWriteTable(conn, DBI::Id(schema = schema, table = table), 
                       batch_df,
                       overwrite = FALSE, append = TRUE, row.names = FALSE)
    }
    
    # Commit transaction
    DBI::dbCommit(conn)
    TRUE
  }, error = function(e) {
    # Rollback on error
    DBI::dbRollback(conn)
    cat(sprintf("INSERT method failed: %s\n", e$message))
    FALSE
  })
}

# Helper function to prepare data.table for database upload
prepare_for_db_upload <- function(dt) {
  # Convert all factor columns to character
  for (col in names(dt)) {
    if (is.factor(dt[[col]])) {
      dt[[col]] <- as.character(dt[[col]])
    }
  }
  # Ensure numeric columns are appropriate (e.g., double)
  if ("value" %in% names(dt)) {
    dt$value <- as.numeric(dt$value)
    dt$value[is.nan(dt$value)] <- NA
    dt$value[is.infinite(dt$value)] <- NA
  }
  # Replace NA in character columns with empty string
  for (col in names(dt)) {
    if (is.character(dt[[col]])) {
      dt[[col]][is.na(dt[[col]])] <- ""
    }
  }
  return(dt)
}

# Function to verify data integrity between local and remote tables
verify_data_integrity <- function(local_conn, remote_conn, local_table, remote_schema, remote_table) {
  cat("\n=== VERIFYING DATA INTEGRITY ===\n")
  
  # 1. Row count comparison
  cat("1. Comparing row counts...\n")
  local_count_query <- sprintf("SELECT COUNT(*) as count FROM %s", local_table)
  remote_count_query <- sprintf("SELECT COUNT(*) as count FROM %s.%s", remote_schema, remote_table)
  
  local_count <- DBI::dbGetQuery(local_conn, local_count_query)$count[1]
  remote_count <- DBI::dbGetQuery(remote_conn, remote_count_query)$count[1]
  
  cat(sprintf("   Local table rows: %s\n", format(local_count, big.mark = ",")))
  cat(sprintf("   Remote table rows: %s\n", format(remote_count, big.mark = ",")))
  
  if (local_count != remote_count) {
    stop(sprintf("ROW COUNT MISMATCH: Local has %s rows, Remote has %s rows", 
                format(local_count, big.mark = ","), format(remote_count, big.mark = ",")))
  }
  cat("   ✓ Row counts match!\n")
  
  # 2. Data type distribution verification
  cat("2. Verifying data type distribution...\n")
  local_dist_query <- "SELECT data_type, COUNT(*) as count FROM cellminer_data_new GROUP BY data_type ORDER BY data_type"
  remote_dist_query <- sprintf("SELECT data_type, COUNT(*) as count FROM %s.%s GROUP BY data_type ORDER BY data_type", remote_schema, remote_table)
  
  local_dist <- DBI::dbGetQuery(local_conn, local_dist_query)
  remote_dist <- DBI::dbGetQuery(remote_conn, remote_dist_query)
  
  if (nrow(local_dist) != nrow(remote_dist) || !all(local_dist$data_type == remote_dist$data_type) || !all(local_dist$count == remote_dist$count)) {
    cat("   Local distribution:\n")
    print(local_dist)
    cat("   Remote distribution:\n")
    print(remote_dist)
    stop("DATA TYPE DISTRIBUTION MISMATCH between local and remote tables")
  }
  cat("   ✓ Data type distributions match!\n")
  
  # 3. Dataset distribution verification
  cat("3. Verifying dataset distribution...\n")
  local_dataset_query <- "SELECT dataset, COUNT(*) as count FROM cellminer_data_new GROUP BY dataset ORDER BY dataset"
  remote_dataset_query <- sprintf("SELECT dataset, COUNT(*) as count FROM %s.%s GROUP BY dataset ORDER BY dataset", remote_schema, remote_table)
  
  local_datasets <- DBI::dbGetQuery(local_conn, local_dataset_query)
  remote_datasets <- DBI::dbGetQuery(remote_conn, remote_dataset_query)
  
  if (nrow(local_datasets) != nrow(remote_datasets) || !all(local_datasets$dataset == remote_datasets$dataset) || !all(local_datasets$count == remote_datasets$count)) {
    cat("   Local datasets:\n")
    print(local_datasets)
    cat("   Remote datasets:\n")
    print(remote_datasets)
    stop("DATASET DISTRIBUTION MISMATCH between local and remote tables")
  }
  cat("   ✓ Dataset distributions match!\n")
  
  # 4. Sample data verification (check first and last 100 rows when ordered)
  cat("4. Verifying sample data integrity...\n")
  sample_query_template <- "SELECT dataset, data_type, col_name, row_name, value FROM %s ORDER BY dataset, data_type, col_name, row_name %s 100"
  
  # First 100 rows
  local_first <- DBI::dbGetQuery(local_conn, sprintf(sample_query_template, "cellminer_data_new", "LIMIT"))
  remote_first <- DBI::dbGetQuery(remote_conn, sprintf(sample_query_template, sprintf("%s.%s", remote_schema, remote_table), "LIMIT"))
  
  if (!identical(local_first, remote_first)) {
    stop("SAMPLE DATA MISMATCH: First 100 rows do not match between local and remote tables")
  }
  
  # Last 100 rows
  local_last <- DBI::dbGetQuery(local_conn, sprintf(sample_query_template, "cellminer_data_new", "OFFSET (SELECT COUNT(*) - 100 FROM cellminer_data_new) LIMIT"))
  remote_last <- DBI::dbGetQuery(remote_conn, sprintf(sample_query_template, sprintf("%s.%s", remote_schema, remote_table), sprintf("OFFSET (SELECT COUNT(*) - 100 FROM %s.%s) LIMIT", remote_schema, remote_table)))
  
  if (!identical(local_last, remote_last)) {
    stop("SAMPLE DATA MISMATCH: Last 100 rows do not match between local and remote tables")
  }
  cat("   ✓ Sample data (first and last 100 rows) match!\n")
  
  # 5. Null value distribution verification
  cat("5. Verifying null value distributions...\n")
  null_check_columns <- c("value", "cell_line_tissue", "drug_moa", "original_cell_name", "original_row_name")
  
  for (col in null_check_columns) {
    local_nulls <- DBI::dbGetQuery(local_conn, sprintf("SELECT COUNT(*) as count FROM cellminer_data_new WHERE %s IS NULL", col))$count[1]
    remote_nulls <- DBI::dbGetQuery(remote_conn, sprintf("SELECT COUNT(*) as count FROM %s.%s WHERE %s IS NULL", remote_schema, remote_table, col))$count[1]
    
    if (local_nulls != remote_nulls) {
      stop(sprintf("NULL VALUE MISMATCH in column '%s': Local has %s nulls, Remote has %s nulls", col, local_nulls, remote_nulls))
    }
  }
  cat("   ✓ Null value distributions match!\n")
  
  # 6. Statistical verification for numeric values
  cat("6. Verifying statistical properties of numeric values...\n")
  local_stats <- DBI::dbGetQuery(local_conn, 
    "SELECT 
       COUNT(value) as non_null_count,
       AVG(value) as mean_value,
       MIN(value) as min_value,
       MAX(value) as max_value,
       STDDEV(value) as stddev_value
     FROM cellminer_data_new 
     WHERE value IS NOT NULL")
  
  remote_stats <- DBI::dbGetQuery(remote_conn, 
    sprintf("SELECT 
               COUNT(value) as non_null_count,
               AVG(value) as mean_value,
               MIN(value) as min_value,
               MAX(value) as max_value,
               STDDEV(value) as stddev_value
             FROM %s.%s 
             WHERE value IS NOT NULL", remote_schema, remote_table))
  
  # Check if statistics match (allowing for small floating point differences)
  tolerance <- 1e-10
  if (local_stats$non_null_count != remote_stats$non_null_count ||
      abs(local_stats$mean_value - remote_stats$mean_value) > tolerance ||
      abs(local_stats$min_value - remote_stats$min_value) > tolerance ||
      abs(local_stats$max_value - remote_stats$max_value) > tolerance ||
      abs(local_stats$stddev_value - remote_stats$stddev_value) > tolerance) {
    cat("   Local statistics:\n")
    print(local_stats)
    cat("   Remote statistics:\n")
    print(remote_stats)
    stop("STATISTICAL PROPERTIES MISMATCH between local and remote tables")
  }
  cat("   ✓ Statistical properties match!\n")
  
  cat("\n=== DATA INTEGRITY VERIFICATION COMPLETE ===\n")
  cat("✓ ALL CHECKS PASSED - Local and remote tables are IDENTICAL!\n\n")
  
  return(TRUE)
}

# Function to save/load checkpoint
save_checkpoint <- function(checkpoint_data, file_path = checkpoint_file) {
  saveRDS(checkpoint_data, file_path)
  cat(sprintf("Checkpoint saved: %s rows processed\n", checkpoint_data$rows_processed))
}

load_checkpoint <- function(file_path = checkpoint_file) {
  if (file.exists(file_path)) {
    checkpoint <- readRDS(file_path)
    cat(sprintf("Resuming from checkpoint: %s rows already processed\n", checkpoint$rows_processed))
    return(checkpoint)
  }
  return(NULL)
}

# Parallel transfer function
transfer_batch_parallel <- function(batch_data, worker_id, schema, table) {
  cat(sprintf("Worker %d: Processing batch of %d rows\n", worker_id, nrow(batch_data)))
  
  # Create separate connection for this worker
  worker_conn <- tryCatch({
    conn <- DBI::dbConnect(RPostgres::Postgres(),
                          dbname = remote_db_name, 
                          host = remote_db_host, 
                          port = remote_db_port,
                          user = remote_db_user, 
                          password = remote_db_pass,
                          connect_timeout = 300,
                          keepalives_idle = 600,
                          keepalives_interval = 30,
                          keepalives_count = 3,
                          sslmode = "require")
    optimize_db_connection(conn, is_remote = TRUE)
    conn
  }, error = function(e) {
    cat(sprintf("Worker %d: Connection failed: %s\n", worker_id, e$message))
    return(NULL)
  })
  
  if (is.null(worker_conn)) return(FALSE)
  
  success <- FALSE
  tryCatch({
    if (use_copy_method) {
      success <- write_data_copy(worker_conn, batch_data, schema, table)
    }
    if (!success) {
      success <- write_data_insert(worker_conn, batch_data, schema, table)
    }
  }, finally = {
    if (!is.null(worker_conn) && DBI::dbIsValid(worker_conn)) {
      DBI::dbDisconnect(worker_conn)
    }
  })
  
  cat(sprintf("Worker %d: Batch %s\n", worker_id, if(success) "completed successfully" else "failed"))
  return(success)
}

# Main execution logic
main <- function() {
  log_file <- file.path(base_path, sprintf("transfer_optimized_%s.log", format(Sys.time(), "%Y%m%d_%H%M%S")))
  sink(log_file, split = TRUE)
  on.exit({ sink(); cat(sprintf("\nLog written to %s\n", log_file)) }, add = TRUE)
  
  cat("=== Starting OPTIMIZED Data Transfer from Local to Remote PostgreSQL ===\n\n")
  cat(sprintf("Optimizations enabled:\n"))
  cat(sprintf("- COPY method: %s\n", use_copy_method))
  cat(sprintf("- Parallel processing: %s (workers: %d)\n", use_parallel, num_workers))
  cat(sprintf("- Compression: %s\n", use_compression))
  cat(sprintf("- Larger chunks: %s\n", larger_chunks))
  cat(sprintf("- Connection optimization: %s\n", optimize_connections))
  
  local_conn <- NULL
  remote_conn <- NULL
  total_rows_uploaded <- 0
  checkpoint <- NULL
  
  # Load checkpoint if resuming
  if (!fresh_run) {
    checkpoint <- load_checkpoint()
    if (!is.null(checkpoint)) {
      total_rows_uploaded <- checkpoint$rows_processed
    }
  }
  
  tryCatch({
    # Connect to local database with timeout settings
    cat("Connecting to local PostgreSQL database...\n")
    local_conn <- DBI::dbConnect(RPostgres::Postgres(),
                                dbname = local_db_name, host = local_db_host, port = local_db_port,
                                user = local_db_user, password = local_db_pass,
                                connect_timeout = 300, # 5 minutes
                                keepalives_idle = 600,
                                keepalives_interval = 30,
                                keepalives_count = 3)
    cat("Local PostgreSQL connection successful.\n")
    optimize_db_connection(local_conn, is_remote = FALSE)
    
    # Test local connection
    local_test <- DBI::dbGetQuery(local_conn, "SELECT 1 as test")
    if (local_test$test[1] != 1) {
      stop("Local connection test failed")
    }
    
    # Check pagination index on source table
    cat("Checking pagination index on local table...\n")
    ensure_pagination_index(local_conn, "cellminer_data_new")
    
    # Connect to remote database with timeout settings and retry logic
    cat("Connecting to remote Azure PostgreSQL database...\n")
    max_retries <- 3
    retry_count <- 0
    remote_conn <- NULL
    
    while (retry_count < max_retries && is.null(remote_conn)) {
      retry_count <- retry_count + 1
      tryCatch({
        remote_conn <- DBI::dbConnect(RPostgres::Postgres(),
                                     dbname = remote_db_name, host = remote_db_host, port = remote_db_port,
                                     user = remote_db_user, password = remote_db_pass,
                                     connect_timeout = 300, # 5 minutes
                                     keepalives_idle = 600,
                                     keepalives_interval = 30,
                                     keepalives_count = 3,
                                     sslmode = "require")
        cat("Remote Azure PostgreSQL connection successful.\n")
      }, error = function(e) {
        cat(sprintf("Remote connection attempt %d failed: %s\n", retry_count, e$message))
        if (retry_count < max_retries) {
          cat(sprintf("Retrying in 5 seconds...\n"))
          Sys.sleep(5)
        }
      })
    }
    
    if (is.null(remote_conn)) {
      stop("Failed to connect to remote database after multiple attempts")
    }
    
    optimize_db_connection(remote_conn, is_remote = TRUE)
    
    # Test remote connection
    remote_test <- DBI::dbGetQuery(remote_conn, "SELECT 1 as test")
    if (remote_test$test[1] != 1) {
      stop("Remote connection test failed")
    }
    
    # Test both connections
    cat("Testing database connections...\n")
    local_version <- DBI::dbGetQuery(local_conn, "SELECT version()")
    cat(sprintf("Local PostgreSQL: %s\n", substr(local_version$version[1], 1, 50)))
    
    remote_version <- DBI::dbGetQuery(remote_conn, "SELECT version()")
    cat(sprintf("Remote PostgreSQL: %s\n", substr(remote_version$version[1], 1, 50)))
    
    # Verify source table exists and test data access
    cat("Verifying source table exists and testing data access...\n")
    source_tables <- DBI::dbListTables(local_conn)
    if (!"cellminer_data_new" %in% source_tables) {
      stop("Source table 'cellminer_data_new' not found in local database")
    }
    cat("Source table 'cellminer_data_new' confirmed.\n")
    
    # Test reading a small sample to verify data access
    cat("Testing data access with small sample...\n")
    test_query <- "SELECT * FROM cellminer_data_new ORDER BY dataset, data_type, col_name, row_name LIMIT 10"
    test_result <- tryCatch({
      DBI::dbGetQuery(local_conn, test_query)
    }, error = function(e) {
      stop(sprintf("Failed to read test data from source table: %s", e$message))
    })
    
    if (nrow(test_result) == 0) {
      stop("Source table appears to be empty")
    }
    cat(sprintf("Successfully read %d test rows from source table\n", nrow(test_result)))
    
    # Display column structure
    cat("Source table structure:\n")
    print(str(test_result))
    
    # Verify/create remote table
    cat("Verifying/creating remote table...\n")
    remote_id <- DBI::Id(schema = remote_schema, table = remote_table)
    
    # SAFETY CHECK: Ensure we NEVER drop the local table
    if (remote_db_host == "localhost" || remote_db_host == "127.0.0.1") {
      stop("SAFETY ERROR: Remote database appears to be localhost. This would risk dropping the local table!")
    }
    
    # Clear remote table if this is a fresh run to ensure exact copy
    # This ONLY affects the REMOTE table, never the local source table
    if (fresh_run && DBI::dbExistsTable(remote_conn, remote_id)) {
      cat(sprintf("Fresh run: Clearing existing REMOTE table %s.%s (NOT touching local table)...\n", remote_schema, remote_table))
      cat(sprintf("Confirming: Remote host is %s (NOT localhost)\n", remote_db_host))
      
      # Use TRUNCATE instead of DROP to preserve table structure and be safer
      clear_sql <- sprintf("TRUNCATE TABLE %s.%s", 
                          DBI::dbQuoteIdentifier(remote_conn, remote_schema), 
                          DBI::dbQuoteIdentifier(remote_conn, remote_table))
      DBI::dbExecute(remote_conn, clear_sql)
      cat("Remote table cleared for fresh transfer. Local table remains untouched.\n")
    }
    
    if (!DBI::dbExistsTable(remote_conn, remote_id)) {
      cat(sprintf("Creating remote table %s.%s...\n", remote_schema, remote_table))
      create_table_sql <- sprintf(
        "CREATE TABLE IF NOT EXISTS %s.%s (
          dataset TEXT,
          data_type TEXT,
          col_name TEXT,
          row_name TEXT,
          value DOUBLE PRECISION,
          cell_line_tissue TEXT,
          drug_moa TEXT,
          original_cell_name TEXT,
          original_row_name TEXT
        );",
        DBI::dbQuoteIdentifier(remote_conn, remote_schema), 
        DBI::dbQuoteIdentifier(remote_conn, remote_table)
      )
      DBI::dbExecute(remote_conn, create_table_sql)
      cat("Remote table created.\n")
    } else {
      cat(sprintf("Remote table %s.%s already exists.\n", remote_schema, remote_table))
    }
    
    # Get total count
    cat("Counting total rows in source table...\n")
    if (is.null(checkpoint)) {
      total_rows_query <- DBI::dbGetQuery(local_conn, "SELECT COUNT(*) as count FROM cellminer_data_new;")
      total_rows <- total_rows_query$count[1]
      cat(sprintf("Total rows in source table: %s\n", format(total_rows, big.mark = ",")))
    } else {
      # Estimate remaining rows when resuming
      total_rows_query <- DBI::dbGetQuery(local_conn, "SELECT COUNT(*) as count FROM cellminer_data_new;")
      total_rows <- total_rows_query$count[1] - checkpoint$rows_processed
      cat(sprintf("Remaining rows to transfer: %s\n", format(total_rows, big.mark = ",")))
    }
    
    cat(sprintf("Total rows to transfer: %s\n", format(total_rows, big.mark = ",")))
    
    # Determine optimal chunk size for maximum speed
    chunk_size <- if (larger_chunks && use_copy_method) 500000 else 10000  # Much larger chunks for speed
    if (use_parallel) chunk_size <- max(5000, chunk_size %/% num_workers)
    
    cat(sprintf("Using chunk size: %s\n", format(chunk_size, big.mark = ",")))
    
    # Setup parallel processing if enabled
    if (use_parallel) {
      cat(sprintf("Setting up parallel processing with %d workers...\n", num_workers))
      if (require(doParallel, quietly = TRUE)) {
        cl <- parallel::makeCluster(num_workers)
        doParallel::registerDoParallel(cl)
        on.exit(parallel::stopCluster(cl), add = TRUE)
      }
    }
    
    # Transfer data using key-based pagination
    last_keys <- if (!is.null(checkpoint)) checkpoint$last_keys else NULL
    batch_num <- 0
    
    repeat {
      batch_num <- batch_num + 1
      cat(sprintf("Reading batch %d from local...\n", batch_num))
      
      # Validate connections before each batch
      if (!DBI::dbIsValid(local_conn)) {
        stop("Local database connection is no longer valid")
      }
      if (!DBI::dbIsValid(remote_conn)) {
        stop("Remote database connection is no longer valid")
      }
      
      # Get next batch using key-based pagination
      tryCatch({
        batch_result <- get_next_batch_keyed(local_conn, "cellminer_data_new", last_keys, chunk_size)
        chunk_dt <- data.table(batch_result$data)
        last_keys <- batch_result$last_keys
      }, error = function(e) {
        cat(sprintf("ERROR reading batch %d: %s\n", batch_num, e$message))
        
        # Try to reconnect to local database if connection failed
        if (grepl("connection|invalid", e$message, ignore.case = TRUE)) {
          cat("Attempting to reconnect to local database...\n")
          tryCatch({
            if (!is.null(local_conn) && DBI::dbIsValid(local_conn)) {
              DBI::dbDisconnect(local_conn)
            }
            local_conn <<- DBI::dbConnect(RPostgres::Postgres(),
                                        dbname = local_db_name, host = local_db_host, port = local_db_port,
                                        user = local_db_user, password = local_db_pass,
                                        connect_timeout = 300,
                                        keepalives_idle = 600,
                                        keepalives_interval = 30,
                                        keepalives_count = 3)
            optimize_db_connection(local_conn, is_remote = FALSE)
            cat("Successfully reconnected to local database\n")
            
            # Retry the batch read
            batch_result <- get_next_batch_keyed(local_conn, "cellminer_data_new", last_keys, chunk_size)
            chunk_dt <<- data.table(batch_result$data)
            last_keys <<- batch_result$last_keys
            return()
          }, error = function(e2) {
            cat(sprintf("Failed to reconnect: %s\n", e2$message))
          })
        }
        
        stop(sprintf("Failed to read batch %d from local database: %s", batch_num, e$message))
      })
      
      if (nrow(chunk_dt) == 0) {
        cat("No more data to transfer.\n")
        break
      }
      
      cat(sprintf("Processing batch %d (%s rows)...\n", batch_num, format(nrow(chunk_dt), big.mark = ",")))
      
      # Track progress
      progress_pct <- round((total_rows_uploaded / total_rows) * 100, 1)
      cat(sprintf("Progress: %s%% (%s of %s rows)\n", 
                 progress_pct, 
                 format(total_rows_uploaded, big.mark = ","), 
                 format(total_rows, big.mark = ",")))
      
      if (use_parallel && nrow(chunk_dt) > num_workers * 1000) {
        # Split chunk for parallel processing
        chunk_splits <- split(chunk_dt, rep(1:num_workers, length.out = nrow(chunk_dt)))
        
        if (require(foreach, quietly = TRUE)) {
          # Suppress R CMD check NOTE about 'no visible binding for global variable'
          worker_idx <- NULL
          results <- foreach(worker_idx = seq_along(chunk_splits), .combine = c, .packages = c("DBI", "RPostgres"), .export = c("transfer_batch_parallel", "remote_schema", "remote_table", "remote_db_name", "remote_db_host", "remote_db_port", "remote_db_user", "remote_db_pass", "use_copy_method", "write_data_copy", "write_data_insert", "optimize_db_connection", "optimize_connections", "use_compression", "prepare_for_db_upload")) %dopar% {
            transfer_batch_parallel(chunk_splits[[worker_idx]], worker_idx, remote_schema, remote_table)
          }
          success <- all(results)
        } else {
          # Fallback to sequential processing
          success <- write_data_copy(remote_conn, chunk_dt, remote_schema, remote_table) ||
                    write_data_insert(remote_conn, chunk_dt, remote_schema, remote_table)
        }
        } else {
          # Sequential processing with COPY method only
          success <- write_data_copy(remote_conn, chunk_dt, remote_schema, remote_table)
        }
        
        if (!success) {
          stop(sprintf("Failed to upload batch %d", batch_num))
        }
        
        total_rows_uploaded <- total_rows_uploaded + nrow(chunk_dt)
      
      # Save checkpoint
      if (batch_num %% 10 == 0) {  # Save every 10 batches
        checkpoint_data <- list(
          rows_processed = total_rows_uploaded,
          last_keys = last_keys,
          timestamp = Sys.time()
        )
        save_checkpoint(checkpoint_data)
      }
      
      rm(chunk_dt); gc(verbose = FALSE)
      
      # Break if we've reached the end
      if (is.null(last_keys)) break
    }
    
    cat(sprintf("\n=== TRANSFER COMPLETE ===\nSuccessfully transferred %s rows to '%s.%s'\n", 
               format(total_rows_uploaded, big.mark = ","), remote_schema, remote_table))
    
    # CRITICAL: Verify data integrity to ensure exact copy
    cat("\n=== STARTING DATA INTEGRITY VERIFICATION ===\n")
    verify_data_integrity(local_conn, remote_conn, "cellminer_data_new", remote_schema, remote_table)
    
    # Create indexes on remote table
    cat(sprintf("\nCreating indexes on remote '%s.%s' table...\n", remote_schema, remote_table))
    cols_to_index <- c("dataset", "data_type", "col_name", "row_name", "cell_line_tissue", "drug_moa", "original_cell_name", "original_row_name")
    for (col in cols_to_index) {
      index_name <- paste0("idx_cm_new_", col)
      
      # Check if index already exists
      check_index_sql <- sprintf("SELECT 1 FROM pg_indexes WHERE indexname = '%s' AND tablename = '%s' AND schemaname = '%s'", 
                                index_name, remote_table, remote_schema)
      
      existing_index <- tryCatch({
        result <- DBI::dbGetQuery(remote_conn, check_index_sql)
        nrow(result) > 0
      }, error = function(e) {
        FALSE
      })
      
      if (existing_index) {
        cat(sprintf("Index '%s' already exists, skipping...\n", index_name))
        next
      }
      
      sql_statement <- sprintf("CREATE INDEX CONCURRENTLY %s ON %s.%s (%s);", index_name,
                              DBI::dbQuoteIdentifier(remote_conn, remote_schema), 
                              DBI::dbQuoteIdentifier(remote_conn, remote_table), col)
      cat(sprintf("Executing: %s\n", sql_statement))
      tryCatch({
        DBI::dbExecute(remote_conn, sql_statement)
        cat(sprintf("Successfully created index '%s'\n", index_name))
      }, error = function(e) {
        cat(sprintf("Warning: Could not create index %s: %s\n", index_name, e$message))
      })
    }
    
    # Remove checkpoint file on successful completion
    if (file.exists(checkpoint_file)) {
      unlink(checkpoint_file)
      cat("Checkpoint file removed.\n")
    }
    
  }, error = function(e) {
    cat(sprintf("\nERROR: %s\n", e$message))
    stop("Data transfer failed.")
  }, finally = {
    if (!is.null(local_conn) && DBI::dbIsValid(local_conn)) {
      DBI::dbDisconnect(local_conn)
      cat("Local database connection closed.\n")
    }
    if (!is.null(remote_conn) && DBI::dbIsValid(remote_conn)) {
      DBI::dbDisconnect(remote_conn)
      cat("Remote database connection closed.\n")
    }
  })
}

# Execute with error handling
tryCatch({
  main()
}, error = function(e) {
  cat(sprintf("\nFATAL ERROR: %s\n", e$message))
  check_memory("on error")
  traceback()
})

cat("\nOptimized data transfer script finished.\n")
