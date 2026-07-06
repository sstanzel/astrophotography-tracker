import os
import pandas as pd

def process_directories_in_current_folder():
    # Get a list of all items in the current directory
    items = os.listdir('.')
    
    # Filter out files, keep only directories
    directories = [item for item in items if os.path.isdir(item)]
    
    # Process each directory to extract names and dates
    processed_data = []
    for directory in directories:
        parts = directory.rsplit("_", 1)
        full_name = directory  # Keep the full directory name
        name = parts[0]
        date = parts[1] if len(parts) > 1 and parts[1].replace("-", "").isdigit() else None
        processed_data.append({
            "Full Directory Name": full_name,
            "Directory Name": name,
            "Date (YYYY-MM-DD)": date
        })
    
    # Create a DataFrame
    df = pd.DataFrame(processed_data)
    
    # Define output CSV file name
    output_file = "processed_directories_with_full_name.csv"
    
    # Save the DataFrame to a CSV file
    df.to_csv(output_file, index=False)
    print(f"Processed directory information saved to {output_file}")

if __name__ == "__main__":
    process_directories_in_current_folder()