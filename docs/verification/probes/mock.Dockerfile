FROM python:3.12-slim
COPY mock-server.py /srv/server.py
CMD ["python", "/srv/server.py"]
