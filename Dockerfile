FROM python:3.11
ADD doshmon.py .
ADD requirements.txt .
RUN pip install -r requirements.txt --root-user-action=ignore
CMD ["python", "doshmon.py"]
