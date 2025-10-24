const rawUrl = process.env.REACT_APP_API_URL;
const url = rawUrl.endsWith('/') ? rawUrl : rawUrl + '/';

const headers = new Headers();
headers.append('Content-Type', 'application/json');

const endpoints = {
    experiments: "fe_experiments",
    runs: "fe_runs",
    metrics: "fe_metrics_available",
    data: "fe_metrics"
}

export const HTTP = {

    fetchData: (endpoint, param = "") => {
        console.log("GET: " + endpoint + param); // debugging
        return fetch(url + endpoint + param, { headers })
            .then(response => response.json())
            .then((json) => {
                return json;
            })
            .catch((error) => {
                alert("\n" + error.message + "\n\nCheck browser console for more information. ");
                return [];
            })
    },

    fetchExperiments: async (param = "") => {
        return new Promise((resolve) => {
            HTTP.fetchData(endpoints.experiments, param).then((json) => {
                let parsed = [];
                json.forEach(data => {
                    parsed.push({
                        "id": data["experiment_id"],
                        "name": data["name"]
                    })
                })
                resolve(parsed);
            });
        });
    },

    fetchRuns: async (param = "") => {
        return new Promise((resolve) => {
            HTTP.fetchData(endpoints.runs, param).then((json) => {
                let parsed = [];
                json.forEach(data => {
                    if (data["workload"] === null) {
                        data["workload"] = "null";
                    }
                    let params = data["params"];
                    let workloadId = data["experiment_id"] + "-" + params["workload"];
                    parsed.push({
                        "name": data["run_uuid"],
                        "run_name": data["name"],
                        "experimentId": data["experiment_id"],
                        "status": data["status"],
                        "startTime": data["start_time"],
                        "duration": data["duration"],
                        "letter": params["letter"],
                        "workload": workloadId,
                        "params": params,
                    })
                })
                resolve(parsed);
            });
        });
    },

    fetchMetrics: async (runs) => {
        if (runs.length > 0) {
            let param = "?run_uuid=in.(";
            runs.forEach(run => {
                param = param + '"' + run.name + '",';
            });
            param = param.substring(0, param.length - 1) + ")";
            console.log(param);
            return new Promise((resolve) => {
                HTTP.fetchData(endpoints.metrics, param).then((json) => {
                    let uniqueMetrics = [];
                    json.forEach(metric => {
                        const metricIndex = uniqueMetrics.indexOf(metric.key);
                        if (metricIndex === -1) {
                            uniqueMetrics.push(metric.key);
                        }
                    });
                    resolve(uniqueMetrics.sort());
                });
            });
        }
        else {
            return [];
        }
    },

    fetchChart: async (runs, metric) => {
        if (runs.length > 0) {
            let param = "?run_uuid=in.(";
            runs.forEach(run => {
                param = param + '"' + run.name + '",';
            });
            param = param.substring(0, param.length - 1) + ")&key=eq." + encodeURIComponent(metric);
            return new Promise((resolve) => {
                HTTP.fetchData(endpoints.data, param).then((json) => {
                    let parsed = [];
                    json.forEach(data => {
                        parsed.push({
                            "name": data["run_uuid"],
                            "metric": data["key"],
                            "step": data["step"],
                            "timestamp": data["timestamp"],
                            "value": data["value"],
                        })
                    })
                    resolve(parsed);
                });
            });
        }
        else {
            console.error("No data found!");
            return [];
        }
    }

}
