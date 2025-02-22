FROM python:3.9

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    build-essential git vim && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /src

RUN git clone https://github.com/python/cpython.git /src/cpython && cd /src/cpython && git checkout 3.10 && \
    pip install jupyter==1.0.0

CMD ["jupyter", "notebook", "--ip=0.0.0.0", "--port=8888", "--no-browser", "--allow-root", "--NotebookApp.token=''"]