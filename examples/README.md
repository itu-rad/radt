# RADT Training Examples

## Server configuration

[![preview](/media/dataflow-white.png)](#readme)

Running RADT requires some infrastructure to be set up, namely:

- **MLFlow instance** for workflow and experiment management. The current version of RADT is designed as an extension on [**MLFlow**](https://mlflow.org/).
- **Relational database** for experiment and metric storage. We use [**PostgreSQL**](https://www.postgresql.org/).
- **S3 database** for artifact tracking, such as log files and traces. We use [**MinIO**](https://min.io/).
- **Visualisation server** for the visualisation front-end (React) plus a small Node API.

We provide two options for deploying these requirements:

### **1. Docker Compose (Recommended)**

The easiest way of deploying RADT, including the MLFlow instance and data storage, is via [Docker Compose](https://github.com/docker/compose). This deploys all the prerequisites as separate containers.

In order to deploy RADT using docker: 

1. Clone this repo to your server
2. From the repo root, start the stack:

```bash
docker compose up
```

The nginx service exposes port 80 with the following routes:

- Frontend UI: http://<host>/radt/
- API for frontend: http://<host>/api/
- MLflow: http://<host>/mlflow/
- MinIO console: http://<host>/minio/

### **2. Docker containers**

The containers can also be deployed manually/individually if desired:

- [MLFlow](https://mlflow.org/docs/latest/docker.html)
- [MinIO](https://hub.docker.com/r/minio/minio/)
- [PostgreSQL](https://hub.docker.com/_/postgres)
- [RADT Frontend](/frontend/)
- [RADT Frontend API](/frontend/server/)

### **3. From source**

If you do not want to use containers, you can deploy the frontend manually.
Clone this repository and follow the instructions in [frontend](/frontend) to build the visualization environment manually.
Keep in mind that in this case you will need to set up the rest of the services(MLFlow, database etc.) manually as well.

## Client configuration

Before running examples make sure you install the requirements. We recommend using Anaconda for environment management. Examples come bundled with a `conda.yaml` file which can be used to create the requisite environment:

```bash
conda env create -f conda.yaml
```

Furthermore, MLFlow requires a selection of environment variables to be set in the environment before operation:

```bash
conda env config vars set MLFLOW_TRACKING_USERNAME=
conda env config vars set MLFLOW_TRACKING_PASSWORD=
conda env config vars set MLFLOW_TRACKING_URI=
```

The `MLFLOW_TRACKING_USERNAME` and `MLFLOW_TRACKING_PASSWORD` fields are only required when authorisation is enabled for MLFlow.

## Using Nsight
Nsight tracking can be enabled by using the respective listeners, e.g. `nsys` or `ncu`.
By default, these trackers will only track things that are within the `profile` NVTX range. This allows for targeted tracking, which is very helpful when using Nsight. 
It is critical that you use the official `nvtx` python library, as most other libraries (including PyTorch's native NVTX support) do not use `RegistredString` which is required for this kind of filtering.

Example:
```python
>>> import nvtx
>>> range_id = nvtx.start_range("profile")
>>> (... training loop ...)
>>> nvtx.end_range(range_id)
```

Note that for most projects, the amount of data collected will be extremely large. It is recommended to only mark **up to a couple of iterations** for tracing in order to reduce the quantity of data.

When using NCU, you can use the `ncuattach` to run an Nsight Compute live session instead. This is usually more practical than writing to a log file but requires an active connection to your server.

## Advanced tracking options via context

If you want to have more control over what is logged, you can encapsulate your training loop in the RADT context. This allows for logging of ML metrics among other MLFlow functions:

```py
import radt

with radt.run.RADTBenchmark() as run:
  # training loop
  run.log_metric("Metric A", amount)
  run.log_artifact("artifact.file")
```
All methods and functions under `mlflow` are accessible this way. These functions are disabled when running the codebase without `radt`, ensuring code flexibility.

## Running Examples

All examples should run via `radt <script>.py` unless specified.
radT will automatically supply MLproject files for MLFlow to function correctly (under MLFlow mode) or launch MLFlow directly (under direct mode).

**Examples should work out of the box using the supplied conda environment!**

## Other Examples

Please feel free to contribute examples!
