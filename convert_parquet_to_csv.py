import pandas as pd
import os

# Read the parquet file
parquet_file = "master_20260107.parquet"
parquet_path = os.path.join(os.getcwd(), parquet_file)

print(f"Reading parquet file: {parquet_path}")
df = pd.read_parquet(parquet_path)

# Create CSV filename
csv_file = parquet_file.replace('.parquet', '.csv')
csv_path = os.path.join(os.getcwd(), csv_file)

print(f"Converting to CSV...")
print(f"Shape: {df.shape}")

# Save as CSV
df.to_csv(csv_path, index=False)

print(f"Successfully saved as: {csv_path}")
