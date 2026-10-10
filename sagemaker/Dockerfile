FROM python:3.12-slim

WORKDIR /opt/program

COPY requirements-ml.txt /tmp/requirements-ml.txt
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements-ml.txt "boto3>=1.35,<2"

COPY scripts /opt/program/scripts
COPY sagemaker/__init__.py /opt/program/sagemaker/__init__.py
COPY sagemaker/jobs /opt/program/sagemaker/jobs

ENV PYTHONPATH=/opt/program
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python", "-m", "sagemaker.jobs.train"]
