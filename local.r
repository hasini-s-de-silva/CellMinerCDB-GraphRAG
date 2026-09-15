#!/usr/bin/env Rscript
# Load required libraries with error handling
load_packages <- function() {
  # Added RPostgres, DBI for database connection
  required_packages <- c("rcellminer", "Biobase", "data.table", "dplyr", "tidyr", "stringr", "Matrix", "RPostgres", "DBI")
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
  setDTthreads(0) # Use all available threads for faster in-memory processing
}
options(warn = 1)
gc(verbose = FALSE)

# --- USER CONFIGURATION ---
# Define base paths
base_path <- Sys.getenv("CELLMINER_DATA_DIR", unset = normalizePath(".", mustWork = FALSE))

# >> RESUME CONFIGURATION <<
# To resume a failed run, add the names of successfully completed datasets here.
# For example: skip_datasets <- c("GDSC", "CCLE")
skip_datasets <- c() # For a fresh run, leave this empty: c()

# --- END USER CONFIGURATION ---


# Define primary file locations
files <- list(
  # Original Datasets
  gdsc_drug = file.path(base_path, "gdscData/data/drugData.RData"),
  gdsc_mol = file.path(base_path, "gdscData/data/molData.RData"),
  ccle_drug = file.path(base_path, "ccleData/data/drugData.RData"),
  ccle_mol = file.path(base_path, "ccleData/data/molData.RData"),

  # New Datasets
  nciSarcoma_drug = file.path(base_path, "nciSarcomaData/data/drugData.RData"),
  nciSarcoma_mol = file.path(base_path, "nciSarcomaData/data/molData.RData"),
  uniSarcoma_drug = file.path(base_path, "uniSarcomaData/data/drugData.RData"),
  uniSarcoma_mol = file.path(base_path, "uniSarcomaData/data/molData.RData"),
  mdaMills_mol = file.path(base_path, "mdaMillsData/data/molData.RData"),
  ctrp_drug = file.path(base_path, "ctrpData/data/drugData.RData"),
  ctrp_mol = file.path(base_path, "ctrpData/data/molData.RData"),

  # Metadata Files
  cell_match = file.path(base_path, "rcellminerUtilsCDB/cellLineMatchTab.RData"),
  drug_synonym_csv = file.path(base_path, "rcellminerUtilsCDB/Aug8_nsc_cid_smiles.csv")
)

# Attempt to increase memory limits
increase_memory_limits <- function() {
  if (Sys.getenv("R_MAX_VSIZE") == "") {
    Sys.setenv(R_MAX_VSIZE = "100GB")
    cat(sprintf("Set R_MAX_VSIZE to %s\n", Sys.getenv("R_MAX_VSIZE")))
  }
  if (.Platform$OS.type == "windows") {
    tryCatch({
      memory.limit(size = 100000)
      cat(sprintf("Set memory.limit to %d MB\n", memory.limit()))
    }, error = function(e) {
      cat("Could not set memory.limit (not on Windows or insufficient permissions)\n")
    })
  }
  gc(verbose = FALSE)
}

# Function to monitor memory usage
check_memory <- function(label = "") {
  mem_info <- gc(verbose = FALSE)
  used_mb <- sum(mem_info[, 2])
  cat(sprintf("Memory check %s: %.1f MB used\n", label, used_mb))
  return(used_mb)
}

# Function to check for data availability at the start
check_data_availability <- function(file_list) {
  cat("Checking for required data files...\n")
  # We remove NULL entries from the list before checking file existence
  file_list <- file_list[!sapply(file_list, is.null)]
  missing_files <- file_list[!file.exists(unlist(file_list))]
  if (length(missing_files) > 0) {
    cat("ERROR: The following required data files are missing:\n")
    cat(paste(missing_files, collapse = "\n"))
    stop("Aborting script. Please ensure all data files are available at the specified paths.")
  }
  cat("All required data files found.\n\n")
  return(TRUE)
}


# Function to safely load and inspect RData files
safe_load_inspect <- function(file_path, var_name) {
  cat(sprintf("Loading and inspecting %s...\n", basename(file_path)))
  if (!file.exists(file_path)) {
    stop(sprintf("File does not exist: %s", file_path))
  }
  gc(verbose = FALSE)
  env <- new.env()
  load(file_path, envir = env)
  objects <- ls(env)
  cat(sprintf("  Objects in file: %s\n", paste(objects, collapse = ", ")))
  assign(var_name, env, envir = .GlobalEnv)
  return(env)
}

# This function processes a matrix into a single data.table in memory
process_matrix_to_datatable <- function(matrix_data, data_type, dataset_name) {
  cat(sprintf("Processing %s data for %s...\n", data_type, dataset_name))
  if (!is_matrix_like(matrix_data)) {
    cat("  Input is not a recognised matrix-like object. Skipping.\n")
    return(NULL)
  }
  total_rows <- nrow(matrix_data)
  if (is.null(total_rows) || total_rows == 0) {
    cat("  Matrix appears to have zero rows. Skipping.\n")
    return(NULL)
  }

  tryCatch({
    if (!is.matrix(matrix_data)) matrix_data <- as.matrix(matrix_data)

    dt <- data.table::as.data.table(matrix_data, keep.rownames = "feature_id")
    long_dt <- data.table::melt(dt, id.vars = "feature_id", variable.name = "sample_id", value.name = "value")

    long_dt[, `:=`(data_type = data_type, dataset = dataset_name, row_name = feature_id)]
    long_dt[, feature_id := NULL]
    long_dt <- long_dt[!is.na(value)]

    cat(sprintf("  Processed into a table with %d rows\n", nrow(long_dt)))
    return(long_dt)
  }, error = function(e) {
    cat(sprintf("    Error processing matrix to data.table: %s\n", e$message))
    return(NULL)
  })
}

# MODIFIED: Extract molecular data, using direct path to multiple data types in 'eSetList'.
extract_molecular_data_optimized <- function(molData_env, dataset_name) {
  cat(sprintf("Extracting molecular data from %s dataset...\n", dataset_name))
  all_datatables <- list()
  if (!"molData" %in% ls(molData_env)) {
    return(all_datatables)
  }
  mol_data <- molData_env$molData
  # List of molecular data types to extract
  data_types <- c("exp", "mut", "cop", "mda", "xsq", "mir", "pro", "mtb", "rrb", "met", "muf", "mth", "smt", "var", "fus", "bmt")
  found_any <- FALSE
  for (data_type in data_types) {
    tryCatch({
      if (!is.null(mol_data@eSetList[[data_type]])) {
        expr_matrix <- mol_data@eSetList[[data_type]]@assayData[["exprs"]]
        if (is_matrix_like(expr_matrix)) {
          dt <- process_matrix_to_datatable(expr_matrix, data_type = data_type, dataset_name = dataset_name)
          if (!is.null(dt)) {
            all_datatables[[length(all_datatables) + 1]] <- dt
            found_any <- TRUE
          }
          rm(expr_matrix); gc(verbose = FALSE)
        } else {
          cat(sprintf("    '%s' matrix in eSetList is not matrix-like or is empty.\n", data_type))
        }
      } else {
        # This is normal, not every dataset has every data type.
        # cat(sprintf("    '%s' not found in eSetList.\n", data_type))
      }
    }, error = function(e) {
      cat(sprintf("    Error extracting '%s' matrix from molData@eSetList[['%s']]@assayData: %s\n", data_type, data_type, e$message))
    })
  }
  if (!found_any) {
    cat("    No requested molecular data types found in this molData file.\n")
  }
  return(all_datatables)
}

# Extract drug data, focusing explicitly on 'act' data type.
extract_drug_data_optimized <- function(drugData_env, dataset_name) {
  cat(sprintf("Extracting drug activity data from %s dataset...\n", dataset_name))
  if (!"drugData" %in% ls(drugData_env)) return(NULL)

  tryCatch({
    # getAct specifically retrieves the drug activity matrix.
    act_matrix <- Biobase::exprs(getAct(drugData_env$drugData))
    if (is_matrix_like(act_matrix)) {
      # The data_type is hardcoded to "act", as requested.
      dt <- process_matrix_to_datatable(act_matrix, data_type = "act", dataset_name = dataset_name)
      rm(act_matrix); gc(verbose = FALSE)
      return(dt)
    }
  }, error = function(e) {
    cat(sprintf("  Error extracting drug data: %s\n", e$message))
  })
  return(NULL)
}

# REVISED: Helper to enrich the final data table with metadata.
# This version replaces original identifiers with canonical ones and removes synonym columns.
enrich_datatable <- function(dt, tissue_map, drug_moa_map, drug_canonical_map, cell_canonical_map) {
  cat("  Enriching data with metadata (using joins)...\n")
  if (nrow(dt) == 0) {
      cat("    Input table is empty, skipping enrichment.\n")
      return(dt)
  }

  # >> CORE FIX PART 1: Preserve original names for synonym mapping later <<
  dt[, `:=`(original_cell_name = sample_id, original_row_name = as.character(row_name))]

  # Standardize column names for matching
  dt[, std_col_name := standardize_id(sample_id)]
  dt[, std_row_name := standardize_id(as.character(row_name))]

  # Convert maps to data.tables for joining
  tissue_dt <- if (length(tissue_map) > 0) data.table(std_col_name = names(tissue_map), cell_line_tissue = as.character(tissue_map)) else NULL
  moa_dt <- if (length(drug_moa_map) > 0) data.table(std_row_name = names(drug_moa_map), drug_moa = as.character(drug_moa_map)) else NULL
  drug_canonical_dt <- if (length(drug_canonical_map) > 0) data.table(std_row_name = names(drug_canonical_map), pubchem_cid = as.character(drug_canonical_map)) else NULL
  cell_canonical_dt <- if (length(cell_canonical_map) > 0) data.table(std_col_name = names(cell_canonical_map), cellosaurus_accession = as.character(cell_canonical_map)) else NULL

  # Perform joins to add metadata
  if (!is.null(tissue_dt) && nrow(tissue_dt) > 0) {
    setkey(dt, std_col_name)
    setkey(tissue_dt, std_col_name)
    dt <- tissue_dt[dt]
  }
  if (!is.null(moa_dt) && nrow(moa_dt) > 0) {
    setkey(dt, std_row_name)
    setkey(moa_dt, std_row_name)
    dt <- moa_dt[dt]
  }
  if (!is.null(drug_canonical_dt) && nrow(drug_canonical_dt) > 0) {
    setkey(dt, std_row_name)
    setkey(drug_canonical_dt, std_row_name)
    dt <- drug_canonical_dt[dt]
  }
  if (!is.null(cell_canonical_dt) && nrow(cell_canonical_dt) > 0) {
    setkey(dt, std_col_name)
    setkey(cell_canonical_dt, std_col_name)
    dt <- cell_canonical_dt[dt]
  }

  # >> CORE FIX PART 2 <<
  # Replace original identifiers with canonical ones where available.
  # For drugs (act), replace row_name with pubchem_cid.
  # For all data, replace sample_id with cellosaurus_accession.
  dt[data_type == 'act' & !is.na(pubchem_cid), row_name := pubchem_cid]
  dt[!is.na(cellosaurus_accession), sample_id := cellosaurus_accession]

  # Clean up temporary matching columns and rename sample_id
  dt[, `:=`(std_col_name = NULL, std_row_name = NULL, pubchem_cid = NULL, cellosaurus_accession = NULL)]
  setnames(dt, "sample_id", "col_name")

  cat("  Enrichment complete.\n")
  return(dt)
}

# Helper to standardize IDs for matching
standardize_id <- function(x) {
  toupper(gsub("[^A-Za-z0-9]", "", x))
}

# Helper to check for single, non-delimited IDs
has_single_id <- function(id_str) {
  !is.na(id_str) && !is.null(id_str) && !grepl("[;|, ]", id_str)
}

# Build drug canonical ID map from the CSV file
build_drug_canonical_map <- function(drug_syn_df) {
  canonical_map <- character()
  required_cols <- c("CID", "nci60")
  if (!all(required_cols %in% colnames(drug_syn_df))) return(canonical_map)

  for (i in seq_len(nrow(drug_syn_df))) {
    row <- drug_syn_df[i, ]
    cid <- as.character(row$CID)
    nci60_raw <- as.character(row$nci60)
    
    # Skip if CID is missing or invalid
    if (is.na(cid) || cid == "" || cid == "NA") next

    # Use CID as the canonical ID (PubChem CID)
    canonical_id <- cid
    
    # Process nci60 column - take first value if delimited by "|"
    original_row_name <- ""
    if (!is.na(nci60_raw) && nci60_raw != "" && nci60_raw != "NA") {
      if (grepl("\\|", nci60_raw)) {
        original_row_name <- strsplit(nci60_raw, "\\|")[[1]][1]
      } else {
        original_row_name <- nci60_raw
      }
    }
    
    # Map all synonyms to the canonical CID
    ids_raw <- c(cid, original_row_name, as.character(row$NSC), as.character(row$NAME))
    ids_raw <- ids_raw[sapply(ids_raw, has_single_id)]
    
    for (id in ids_raw) {
      canonical_map[standardize_id(id)] <- canonical_id
    }
  }
  return(canonical_map)
}

# Build cell line canonical ID map from the cell line match table
build_cell_line_canonical_map <- function(cell_line_df) {
  canonical_map <- character()
  required_cols <- c("cellosaurus_accession", "gdscDec15", "ccle")
  if (!all(required_cols %in% colnames(cell_line_df))) return(canonical_map)

  for (i in seq_len(nrow(cell_line_df))) {
    row <- cell_line_df[i, ]
    cellosaurus_id <- as.character(row$cellosaurus_accession)
    gdsc <- as.character(row$gdscDec15)
    ccle <- as.character(row$ccle)

    if (!has_single_id(cellosaurus_id)) next

    # Use Cellosaurus ID as the canonical ID
    canonical_id <- cellosaurus_id
    
    # Map all synonyms to the canonical Cellosaurus ID
    ids_raw <- c(cellosaurus_id, gdsc, ccle, as.character(row$cell_line_name))
    ids_raw <- ids_raw[sapply(ids_raw, has_single_id)]
    
    for (id in ids_raw) {
      canonical_map[standardize_id(id)] <- canonical_id
    }
  }
  return(canonical_map)
}

# MODIFIED: Extract tissue data SOLELY from OncoTree1 within molData@sampleData@samples
get_tissue_map <- function(molData_env, dataset_name = "Unknown") {
    tissue_map <- character()
    if (!("molData" %in% ls(molData_env))) {
        return(tissue_map)
    }
    mol_data <- molData_env$molData
    cat(sprintf("  Extracting tissue data from %s dataset (OncoTree1 only)...\n", dataset_name))

    tryCatch({
        if ("sampleData" %in% slotNames(mol_data)) {
            sample_data_obj <- mol_data@sampleData
            if ("samples" %in% slotNames(sample_data_obj)) {
                samples_data <- sample_data_obj@samples
                sample_ids <- NULL
                oncotree1_values <- NULL

                # Handle both data.frame and list structures for maximum compatibility
                if (is.data.frame(samples_data) && "OncoTree1" %in% colnames(samples_data)) {
                    cat("    Found OncoTree1 column in samples data (data.frame)\n")
                    sample_ids <- rownames(samples_data)
                    oncotree1_values <- as.character(samples_data$OncoTree1)
                } else if (is.list(samples_data) && "OncoTree1" %in% names(samples_data)) {
                    cat("    Found OncoTree1 in samples data (list)\n")
                    oncotree1_values <- as.character(samples_data[["OncoTree1"]])
                    # Try to get sample IDs from 'Name' or rownames, which are often stored in another list element
                    if ("Name" %in% names(samples_data)) {
                        sample_ids <- as.character(samples_data[["Name"]])
                    } else if (!is.null(names(oncotree1_values))) {
                        sample_ids <- names(oncotree1_values)
                    } else {
                        # Fallback if names are not directly available
                        sample_ids <- seq_along(oncotree1_values)
                    }
                }

                if (!is.null(sample_ids) && !is.null(oncotree1_values)) {
                    ids_std <- standardize_id(sample_ids)
                    valid_entries <- !is.na(oncotree1_values) & oncotree1_values != "" & oncotree1_values != "NA"
                    tissue_map[ids_std[valid_entries]] <- oncotree1_values[valid_entries]
                }
            }
        }
        if (length(tissue_map) == 0) {
            cat("    No OncoTree1 tissue data found for this dataset\n")
        } else {
            cat(sprintf("    Added %d tissue entries from OncoTree1 column\n", length(tissue_map)))
        }

    }, error = function(e) {
        cat(sprintf("  Warning: could not extract OncoTree1 tissue map for %s - %s\n", dataset_name, e$message))
    })
    return(tissue_map)
}


# Extract MOA map from a DrugData object
get_moa_map <- function(drugData_env) {
  moa_map <- character()
  if (!"drugData" %in% ls(drugData_env)) return(moa_map)

  tryCatch({
    fd <- Biobase::fData(getAct(drugData_env$drugData))
    if ("MOA" %in% colnames(fd)) {
      ids_std <- standardize_id(rownames(fd))
      moa <- as.character(fd$MOA)
      moa_map[ids_std] <- moa
    }
  }, error = function(e) {
    cat(sprintf("  Warning: could not extract MOA map - %s\n", e$message))
  })
  return(moa_map)
}

# Extend a metadata map with synonyms
extend_map_with_synonyms <- function(metadata_map, synonym_map) {
  if (length(metadata_map) == 0 || length(synonym_map) == 0) return(metadata_map)
  extended <- metadata_map
  for (id in names(metadata_map)) {
    alias_str <- synonym_map[id]
    if (!is.na(alias_str) && nchar(alias_str) > 0) {
      aliases_std <- standardize_id(unlist(strsplit(alias_str, "\\|")))
      extended[aliases_std] <- metadata_map[id]
    }
  }
  return(extended)
}

# Helper to check if an object is matrix-like
is_matrix_like <- function(x) {
  !is.null(x) && (is.matrix(x) || (is.array(x) && length(dim(x)) == 2) || inherits(x, "Matrix"))
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

# Main execution logic
main <- function() {
  log_file <- file.path(base_path, sprintf("extraction_%s.log", format(Sys.time(), "%Y%m%d_%H%M%S")))
  sink(log_file, split = TRUE)
  on.exit({ sink(); cat(sprintf("\nLog written to %s\n", log_file)) }, add = TRUE)

  cat("=== Starting Scalable CellMinerCDB Data Extraction and DB Upload ===\n\n")
  if (length(skip_datasets) > 0) {
      cat(sprintf("Resume mode: Skipping already completed datasets: %s\n", paste(skip_datasets, collapse=", ")))
  }
  increase_memory_limits()

  # --- LOCAL DATABASE CONFIGURATION ---
  db_host <- Sys.getenv("LOCAL_DB_HOST", unset = "localhost")
  db_port <- as.integer(Sys.getenv("LOCAL_DB_PORT", unset = "5432"))
  db_name <- Sys.getenv("LOCAL_DB_NAME", unset = "cellminerdb_new")
  db_user <- Sys.getenv("LOCAL_DB_USER", unset = Sys.info()[["user"]])
  db_pass <- Sys.getenv("LOCAL_DB_PASSWORD", unset = "")

  check_memory("initial")

  # Define all datasets to process in a list for scalability
  datasets_to_process <- list(
    GDSC = list(mol = files$gdsc_mol, drug = files$gdsc_drug),
    CCLE = list(mol = files$ccle_mol, drug = files$ccle_drug),
    NCISARCOMA = list(mol = files$nciSarcoma_mol, drug = files$nciSarcoma_drug),
    UNISARCOMA = list(mol = files$uniSarcoma_mol, drug = files$uniSarcoma_drug),
    MDAMILLS = list(mol = files$mdaMills_mol, drug = NULL), # Mol-data only
    CTRP = list(mol = files$ctrp_mol, drug = files$ctrp_drug)
  )

  # 1. Ensure all files are available before starting
  all_files_to_check <- c(unlist(sapply(datasets_to_process, c)), files$cell_match, files$drug_synonym_csv)
  check_data_availability(all_files_to_check)

  # 2. Build universal canonical ID maps first
  cat("Building universal canonical ID maps...\n")
  drug_syn_df <- read.csv(files$drug_synonym_csv, stringsAsFactors = FALSE)
  drug_canonical_map <- build_drug_canonical_map(drug_syn_df)
  cat(sprintf("  Drug canonical ID map entries: %d\n", length(drug_canonical_map)))

  cell_match_env <- new.env(); load(files$cell_match, envir = cell_match_env)
  cell_line_canonical_map <- build_cell_line_canonical_map(cell_match_env$cellLineMatchTab)
  cat(sprintf("  Cell-line canonical ID map entries: %d\n", length(cell_line_canonical_map)))
  
  rm(drug_syn_df, cell_match_env)
  gc()
  
  conn <- NULL
  total_rows_uploaded <- 0
  
  tryCatch({
    conn <- DBI::dbConnect(RPostgres::Postgres(),
                      dbname = db_name, host = db_host, port = db_port,
                      user = db_user, password = db_pass)
    cat("\nLocal PostgreSQL database connection successful.\n")
    
    if (length(skip_datasets) == 0) {
      cat("Fresh run detected. Dropping existing table 'cellminer_data_new'...\n")
      DBI::dbExecute(conn, "DROP TABLE IF EXISTS cellminer_data_new;")
      cat("  Table dropped.\n")
      
      cat("  Creating table 'cellminer_data_new' with corrected schema...\n")
      create_table_sql <- "
        CREATE TABLE cellminer_data_new (
          dataset TEXT,
          data_type TEXT,
          col_name TEXT, -- Now stores canonical Cellosaurus Accession
          row_name TEXT, -- Now stores canonical PubChem CID for drugs, Gene Symbol for others
          value DOUBLE PRECISION,
          cell_line_tissue TEXT,
          drug_moa TEXT,
          original_cell_name TEXT, -- Preserves original cell name for synonym mapping
          original_row_name TEXT   -- Preserves original row name for synonym mapping
        );
      "
      DBI::dbExecute(conn, create_table_sql)
      cat("  Table 'cellminer_data_new' created.\n")
    }
    
    for (dataset_name in names(datasets_to_process)) {
      if (dataset_name %in% skip_datasets) {
        cat(sprintf("\n--- SKIPPING Dataset: %s (already processed) ---\n", dataset_name))
        next
      }
      
      cat(sprintf("\n--- Processing and Uploading Dataset: %s ---\n", dataset_name))
      paths <- datasets_to_process[[dataset_name]]
      
      current_dataset_tables <- list()
      tissue_map <- character()
      moa_map <- character()
      
      if (!is.null(paths$mol)) {
          mol_env <- safe_load_inspect(paths$mol, "mol_env")
          tissue_map <- get_tissue_map(mol_env, dataset_name)
          mol_tables <- extract_molecular_data_optimized(mol_env, dataset_name)
          current_dataset_tables <- c(current_dataset_tables, mol_tables)
          rm(mol_env, mol_tables); gc()
      }
      
      if (!is.null(paths$drug)) {
          drug_env <- safe_load_inspect(paths$drug, "drug_env")
          moa_map <- get_moa_map(drug_env)
          drug_table <- extract_drug_data_optimized(drug_env, dataset_name)
          if (!is.null(drug_table)) {
            current_dataset_tables[[length(current_dataset_tables) + 1]] <- drug_table
          }
          rm(drug_env, drug_table); gc()
      }
      
      if (length(current_dataset_tables) == 0) {
          cat("No data tables extracted for this dataset. Moving to next.\n")
          next
      }
      
      final_tissue_map <- extend_map_with_synonyms(tissue_map, cell_line_canonical_map)
      final_moa_map <- extend_map_with_synonyms(moa_map, drug_canonical_map)
      
      for (i in seq_along(current_dataset_tables)) {
          single_dt <- current_dataset_tables[[i]]
          
          if(is.null(single_dt) || nrow(single_dt) == 0) {
              next
          }

          dt_type <- unique(single_dt$data_type)
          cat(sprintf("\n  Processing sub-table of type '%s' with %s rows...\n", 
                      dt_type, 
                      format(nrow(single_dt), big.mark=",")))

          enriched_dt <- enrich_datatable(single_dt, final_tissue_map, final_moa_map, drug_canonical_map, cell_line_canonical_map)
          
          if (nrow(enriched_dt) > 0) {
            cat(sprintf("  Uploading %s rows in smaller batches...\n", format(nrow(enriched_dt), big.mark=",")))
            
            chunk_size <- 50000
            num_batches <- ceiling(nrow(enriched_dt) / chunk_size)
            if (num_batches == 0 && nrow(enriched_dt) > 0) num_batches <- 1
            cat(sprintf("  Uploading in %d chunks of %d rows...\n", num_batches, chunk_size))
            
            for (batch_num in 1:num_batches) {
                start_row_idx <- (batch_num - 1) * chunk_size + 1
                end_row_idx <- min(batch_num * chunk_size, nrow(enriched_dt))
                current_chunk_dt <- enriched_dt[start_row_idx:end_row_idx, ]
                if (nrow(current_chunk_dt) > 0) {
                    cat(sprintf("    Uploading chunk %d/%d (%s rows)...\n", batch_num, num_batches, format(nrow(current_chunk_dt), big.mark=",")))
                    prepared_chunk <- prepare_for_db_upload(current_chunk_dt)
                    
                    if (!DBI::dbIsValid(conn)) {
                        cat("      Connection invalid, reconnecting...\n")
                        conn <- DBI::dbConnect(RPostgres::Postgres(),
                                              dbname = db_name, host = db_host, port = db_port,
                                              user = db_user, password = db_pass)
                    }
                    
                    success <- FALSE
                    for (attempt in 1:3) {
                        tryCatch({
                            DBI::dbBegin(conn)
                            DBI::dbWriteTable(conn, "cellminer_data_new", as.data.frame(prepared_chunk), 
                                         overwrite = FALSE, append = TRUE, row.names = FALSE)
                            DBI::dbCommit(conn)
                            success <- TRUE
                        }, error = function(e) {
                            cat(sprintf("      Chunk upload failed (attempt %d): %s\n", attempt, e$message))
                            tryCatch({ DBI::dbRollback(conn); cat("      Transaction rolled back.\n") }, 
                                     error = function(re) { cat(sprintf("      Could not rollback transaction: %s\n", re$message)) })

                            if (attempt < 3) {
                                cat("      Reconnecting to database and retrying...\n")
                                try({ if (!is.null(conn) && DBI::dbIsValid(conn)) DBI::dbDisconnect(conn) }, silent=TRUE)
                                conn <<- DBI::dbConnect(RPostgres::Postgres(),
                                                  dbname = db_name, host = db_host, port = db_port,
                                                  user = db_user, password = db_pass)
                            } else {
                                stop("Chunk processing failed after 3 attempts.")
                            }
                        })
                        if (success) break
                    }
                    
                    total_rows_uploaded <- total_rows_uploaded + nrow(current_chunk_dt)
                }
                rm(current_chunk_dt); gc(verbose=FALSE)
            }
            cat("  Chunk upload complete.\n")
          }
          
          rm(single_dt, enriched_dt); gc(verbose=FALSE)
      }
      
      rm(current_dataset_tables, tissue_map, moa_map, final_tissue_map, final_moa_map); gc()
      check_memory(sprintf("after processing %s", dataset_name))
    }
    
    cat(sprintf("\n=== UPLOAD COMPLETE ===\nSuccessfully uploaded a total of %s rows to the 'cellminer_data_new' table.\n", format(total_rows_uploaded, big.mark=",")))
    
    cat("\nCreating indexes on 'cellminer_data_new' table for faster retrieval...\n")
    cols_to_index <- c("dataset", "data_type", "col_name", "row_name", "cell_line_tissue", "drug_moa", "original_cell_name", "original_row_name")
    
    for (col in cols_to_index) {
      index_name <- paste0("idx_cm_new_", col)
      sql_statement <- sprintf("CREATE INDEX IF NOT EXISTS %s ON cellminer_data_new (%s);", index_name, col)
      cat(sprintf("  Executing: %s\n", sql_statement))
      tryCatch({
        DBI::dbExecute(conn, sql_statement)
      }, error = function(e) {
        cat(sprintf("    Warning: Could not create index %s. It might already exist: %s\n", index_name, e$message))
      })
    }
    
    cat("  Creating composite index on (dataset, data_type)...\n")
    tryCatch({
        DBI::dbExecute(conn, "CREATE INDEX IF NOT EXISTS idx_cm_new_dataset_datatype ON cellminer_data_new (dataset, data_type);")
    }, error = function(e) {
        cat(sprintf("    Warning: Could not create composite index: %s\n", e$message))
    })

    cat("Index creation process complete.\n")
    
  }, error = function(e) {
    cat(sprintf("\nDATABASE ERROR: %s\n", e$message))
    stop("Data upload failed.")
  }, finally = {
    if (!is.null(conn) && DBI::dbIsValid(conn)) {
      DBI::dbDisconnect(conn)
      cat("Database connection closed.\n")
    }
  })
}

# Execute with comprehensive error handling
tryCatch({
  main()
}, error = function(e) {
  cat(sprintf("\nFATAL ERROR: %s\n", e$message))
  check_memory("on error")
  traceback()
})

cat("\nExtraction and upload script finished.\n")
