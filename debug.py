import os
import sys
sys.path.insert(0, '.')

from dotenv import load_dotenv
load_dotenv()

from profiling.profile_loader import load_column_profiles
from scoring.text_scorer import score_text

profiles = load_column_profiles(
    output_dir=os.getenv("PROFILE_OUTPUT_DIR", "output/profiles"),
    database_name="MetadataRepository",
    schema_name="dbo",
    table_name="department_validity_test",
)

dept_profile = profiles.get("Department")
print("Department profile:", dept_profile)
print()
print("Score for '111111':", score_text("111111", dept_profile))
print("Score for '100.10.1234567':", score_text("100.10.1234567", dept_profile))

from profiling.db import read_table_sample
from scoring.row_scorer import score_row

df = read_table_sample(
    database="MetadataRepository",
    schema_name="dbo",
    table_name="department_validity_test",
    sample_rows=50000,
)

# find the row with DepartmentKey=95
row = df[df["DepartmentKey"] == 95].iloc[0]
print("Row values:", row.to_dict())
print()

result = score_row(row=row, column_profiles=profiles, row_score_threshold=0.7)
print("Row score:", result["row_score"])
print("Flagged:", result["flagged"])
print("Details:", result["details"])
print("Skipped columns:", result["skipped_columns"])