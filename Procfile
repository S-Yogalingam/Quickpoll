web: python init_db.py && gunicorn --workers 2 --bind 0.0.0.0:${PORT:-8080} app:app
git add requirements.txt Procfile
git commit -m "Add gunicorn + Procfile for Railway production deployment"
git push origin main
