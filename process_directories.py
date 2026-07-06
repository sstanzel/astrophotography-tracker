import os
import pandas as pd
import re

def parse_directory_name(directory):
    """
    Parses a directory name into its components based on the actual naming convention.
    - Target Designation: Everything before the first '-'
    - Target Name: Text between the first '-' and the first '--' (if present)
    - Camera: One of '585', '2600', or 'R5' after the Target Name
    - Calibration: 'yes' if 'calibration' is in the directory name
    - Acquisition Date: Last segment in YYYY-MM-DD format
    """
    match = re.match(r"([^\-]+)\s*-(.*?)\s*--(.*)", directory)
    
    if not match:
        return {
            "Directory Name": directory,
            "Target Designation": None,
            "Target Name": None,
            "Camera": None,
            "Calibration": None,
            "Acquisition Date": None
        }
    
    target_designation = match.group(1).strip()
    target_name_section = match.group(2).strip()
    
    # Extract Target Name
    target_name_match = re.match(r"(.*?)(?:\s*--|$)", target_name_section)
    target_name = target_name_match.group(1).strip() if target_name_match else None
    
    remaining_parts = match.group(3).split("_")
    
    # Identify Camera from predefined list
    camera = next((part for part in remaining_parts if part in {"585", "2600", "R5"}), None)
    
    # Check for Calibration
    calibration = "yes" if "calibration" in directory.lower() else None
    
    # Extract Acquisition Date (must be in YYYY-MM-DD format)
    acquisition_date = next((part for part in remaining_parts if re.match(r"\d{4}-\d{2}-\d{2}", part)), None)
    
    return {
        "Directory Name": directory,
        "Target Designation": target_designation,
        "Target Name": target_name,
        "Camera": camera,
        "Calibration": calibration,
        "Acquisition Date": acquisition_date
    }

def process_directories_in_current_folder():
    """Processes directories and saves extracted data to a CSV file."""
    
    items = os.listdir('.')
    directories = [item for item in items if os.path.isdir(item)]
    
    processed_data = [parse_directory_name(directory) for directory in directories]
    
    df = pd.DataFrame(processed_data)
    output_file = "processed_directories.csv"
    df.to_csv(output_file, index=False)
    print(f"Processed directory information saved to {output_file}")

if __name__ == "__main__":
    process_directories_in_current_folder()