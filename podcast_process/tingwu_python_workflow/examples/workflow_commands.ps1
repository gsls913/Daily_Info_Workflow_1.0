# Run from this folder:
#   cd D:\path\to\tingwu_python_workflow

# 1. Install dependencies
pip install -r requirements.txt
python -m playwright install chromium

# 2. Create your local credential file from the template
copy tongyi_password.example.txt tongyi_password.txt
notepad tongyi_password.txt

# 3. Log in and save a reusable dedicated-profile state
python tingwu_profile.py auto-login --headed --wait-verification-seconds=60
python tingwu_profile.py status

# 4. Upload an audio file and start transcription
python tingwu_api_upload.py "C:\path\to\audio.m4a"

# Upload multiple files in one command. Internally this creates one Tingwu task per file.
python tingwu_api_upload.py "C:\path\to\a.m4a" "C:\path\to\b.mp3"

# Upload every supported media file in a folder. Files are split into batches of 50.
python tingwu_api_upload.py "C:\path\to\audio_folder" --recursive

# Validate formats, sizes, and durations without uploading
python tingwu_api_upload.py "C:\path\to\audio_folder" --recursive --dry-run

# 5. Export latest completed transcript
python tingwu_export_download.py --out-dir=downloads

# 6. Export by known transId
python tingwu_export_download.py --trans-id=YOUR_TRANS_ID --out-dir=downloads
