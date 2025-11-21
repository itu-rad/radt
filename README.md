<div align="center">
<img src="media/logo_rad.png" height=125 alt="radT logo"/>
</div>

# radT

radT (Resource Aware Data science Tracker) is an extension to MLFlow that simplifies the collection and exploration of hardware metrics of machine learning and deep learning applications. Usually, collecting and processing all the required metrics for these workloads is a hassle. In contrast, RADT is easy to deploy and use, with minimal impact on both performance and time investment. The codebase of RADT is documented and easily expandable.

This work has been published at the SIGMOD workshop DEEM 2023: [Data Management and Visualization for Benchmarking
Deep Learning Training Systems](https://itu-dasyalab.github.io/RAD/publication/papers/DEEM2023.pdf)

```sh
pip install radt
```

<!-- - [Documentation](https://radt.readthedocs.io/) -->
- [Examples](https://github.com/Resource-Aware-Data-systems-RAD/radt/tree/master/examples/#readme)
- [radT on Github](https://github.com/Resource-Aware-Data-Systems-RAD/radt)
- [radT on PyPI](https://pypi.org/project/radt/)

## Releases

The current release is `0.2.16`. radT has been recently released and is frequently receiving updates.

If you find any issues or bugs, feel free to message `titr (at) itu.dk` or open an issue in this repository.

### Changelog
- 0.2.22: Removed default conda dependency
- 0.2.21: Listeners now export system metrics, added name column.
- 0.2.20: Resolve runs being closed when listeners exit.
- 0.2.19: Add free listener, add pytorch data workers to top.
- 0.2.18: Resolved issue of listeners duplicating runs under new mlflow versions.
- 0.2.17: Removed sudo requirement for iostat, renamed iostat fields.
- 0.2.16: Fixed an issue that could lock the process under extreme levels of collocation.
- 0.2.15: RADT now runs correctly on machines that have a corrupt DCGMI installation.
- 0.2.14: Automatically disable the DCGMI listener when DCGMI is not found.
- 0.2.13: Enable RADT on systems without DCGMI.
- 0.2.12: Fixed an issue with dependencies.
- 0.2.11: Workloads are now nested to group them together. Run names include the workload and letter. Improved flexibility of param passthrough.
- 0.2.10: Workload listeners now upload logs when `file` points to a different folder. `rerun` argument now works correctly.
- 0.2.9: Allow text printing while env is setting up.
- 0.2.8: Resolved issue preventing logs from being collected.
- 0.2.7: Resolved race condition that could sometimes disrupt collocated model execution.
- 0.2.6: Resolved synchronisation issues with `.csv` runs.
- 0.2.5: Automatically log `pip`, `conda` package lists and `nvidia-smi` driver info for reproducability.
- 0.2.4: Fixed `rerun` flag, added run names to status
- 0.2.3: Reintroduced manual mode, fixed issue with context attributes, `max_epoch`, `max_time`, and `manual` are now logged as parameters
- 0.2.2: Reintroduced contexts, fixed issue of not having `migedit` as a formal requirement
- 0.2.1: Removed legacy print-statements
- 0.2.0: Moved `radtrun` to be a subcommand in `radt`, reintroduced workload listeners, use `migedit` for mig management, local mode
- 0.1.4: Fixed several minor issues
- 0.1.3: Fixed several bugs that prevented correct logging
- 0.1.0: Initial

## Features

- Wide configuration support including collocation
- Track hardware and software metrics, including Nsight
- Handle continuous streams of data
- Support multiple visualization use-cases
- Filter large amounts of inconsequential data
- Minimal code impact

## Sample usage & getting started

Replace `python` in your training script by `radt`, e.g.:

```py
>>> radt train.py --batch-size 256
```

or, when using virtual environments/conda:

```py
>>> python -m radt train.py --batch-size 256
```

For a complete getting started guide and examples please visit the [Examples](https://github.com/Resource-Aware-Data-systems-RAD/radt/tree/master/examples/#readme).

## Easy to use via automated tracking

radT will automatically track hardware metrics for your application. The listeners will start tracking your application on invocation.

As radT extends MLFlow, you can either use the advanced tracking or use MLFlow to track software metrics (e.g. loss).

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

## CSV syntax for larger experiments

RADT can take the hassle of large experiments off you by training multiple models in succession. Models can even be trained at the same time on different gpus or at the same gpu using a range of collocation schemes.

```csv
Experiment,Workload,Status,Run,Devices,Collocation,    File,    Listeners,Params
0,1,no sharing,,,0,-,../pytorch/cifar10_context.py,smi+top,--batch-size 128
0,2,shared gpu 1,,,0,-,../pytorch/cifar10_context.py,smi+top,--batch-size 128
0,2,shared gpu 2,,,0,-,../pytorch/cifar10_context.py,smi+top,--batch-size 128
0,3,MPS shared gpu 1,,,0,MPS,../pytorch/cifar10_context.py,smi+top,--batch-size 128
0,3,MPS shared gpu 2,,,0,MPS,../pytorch/cifar10_context.py,smi+top,--batch-size 128
0,4,MIG shared gpu 1,,,2,3g.20gb,../pytorch/cifar10_context.py,smi+top,--batch-size 128
0,4,MIG shared gpu 2,,,2,3g.20gb,../pytorch/cifar10_context.py,smi+top,--batch-size 128
```

When interrupted by any means, a csv experiment can be rescheduled to continue from where it left off.

## Supported platforms

- [x] Linux


## Citation

If you need to cite this repository in academic research:
```txt
@inproceedings{robroek2023data,
  title={Data Management and Visualization for Benchmarking Deep Learning Training Systems},
  author={Robroek, Ties and Duane, Aaron and Yousefzadeh-Asl-Miandoab, Ehsan and Tozun, Pinar},
  booktitle={Proceedings of the Seventh Workshop on Data Management for End-to-End Machine Learning},
  pages={1--5},
  year={2023}
}
```


## Contributors

- [Ties Robroek](https://github.com/sipondo)
- [Aaron Duane](https://github.com/aaduane/)
- [Ehsan Yousefzadeh-Asl-Miandoab](https://github.com/ehsanyousefzadehasl)
- [Pınar Tözün](https://github.com/ptozun)

Thank You!

Contributions are welcome. _(Please add yourself to the list)_
