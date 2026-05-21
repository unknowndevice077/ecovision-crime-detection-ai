# 1. pip install roboflow (if not done)
from roboflow import Roboflow

# Get your API Key from: Settings > Workspaces > [Workspace Name] > Roboflow API
rf = Roboflow(api_key="T75WDlN1AfitT3RNDSsU")

# Replace with your actual project and workspace ID from the URL
project = rf.workspace("solarsmartpole").project("ecovision-grtvd")

# This pulls the data directly without waiting for a website zip file
dataset = project.version(2).download("yolov11")

print(f"✅ Success! Dataset is at: {dataset.location}")