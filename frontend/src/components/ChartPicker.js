import React from 'react';
import { HTTP } from '../api';
import Chart from './Chart';
import '../styles/ChartPicker.css';
import DataIcon from '../images/data.svg';
import UploadIcon from '../images/upload.svg';
import DownloadIcon from '../images/download.svg';
import LoadingIcon from '../images/cogwheel.svg';

class ChartPicker extends React.Component {

	constructor(props) {
		super(props);
		this.state = {
			loading: false,
			availableMetrics: [],
			showMetrics: false,
			charts: [],
			pendingChartRenders: 0, // Track pending chart renders
			showLoadingOverlay: false, // New state to track loading overlay visibility
		};

		this.inputField = React.createRef();
		this._pendingChartsFromUrl = null; // hold charts from URL until runs available
	}

	componentDidMount() {
		// Fetch available metrics for any selected runs
		this.fetchMetrics(this.props.pushSelectedRuns);

		// read `charts` param from URL and store for later if present
		const urlParams = new URLSearchParams(window.location.search);
		const chartsParam = urlParams.get('charts');
		if (chartsParam) {
			try {
				const chartMetrics = JSON.parse(decodeURIComponent(chartsParam));
				if (Array.isArray(chartMetrics) && chartMetrics.length > 0) {
					this._pendingChartsFromUrl = chartMetrics;
				}
			} catch (e) {
				console.error("Failed to parse `charts` URL param:", e);
			}
		}

		// attempt to load localStorage charts (as fallback)
		const localCharts = JSON.parse(localStorage.getItem("localCharts"))
		if (localCharts !== null && localCharts.length !== 0) {
			this.setCharts(localCharts);
			this.props.toggleDataPicker(false);
			console.log("GET: charts loaded from localStorage");
			// no need to fetch URL charts if localCharts present
			this._pendingChartsFromUrl = null;
		}
	}

	componentDidUpdate(prevProps) {
		const toHide =  this.props.toHide;
		if (prevProps.toHide !== toHide) {
			const selectedRuns = this.props.pushSelectedRuns;
			this.fetchMetrics(selectedRuns);
		}

		// Fetch metrics if the selected runs have changed
		if (prevProps.pushSelectedRuns !== this.props.pushSelectedRuns) {
			this.fetchMetrics(this.props.pushSelectedRuns);
		}

		// Ensure all charts from the URL are processed
		if (this._pendingChartsFromUrl && this.props.pushSelectedRuns && this.props.pushSelectedRuns.length > 0) {
			let pending = this._pendingChartsFromUrl;
			this._pendingChartsFromUrl = null;

			// Reverse the order of the charts to render right-to-left
			pending = pending.reverse();

			// Track the number of charts being fetched
			this.setState({ pendingChartRenders: pending.length }, async () => {
				// Use Promise.all to ensure all fetches complete
				for (const metric of pending) {
					await this.fetchChartData(metric);
				}
				// Check if all charts are rendered
				this.checkAllChartsRendered();
			});
		}
	}

	// Fetch all available metrics for the current selected runs
	async fetchMetrics(selectedRuns) {
		if (selectedRuns && selectedRuns.length > 0) {
			const data = await HTTP.fetchMetrics(selectedRuns);
			this.setState({ availableMetrics: data });
		} else {
			this.setState({ availableMetrics: [] });
		}
	}

	// toggle metric list visibility 
	toggleMetrics() {
		const toShow = this.state.showMetrics;
		this.setState({ showMetrics: !toShow})
	}

	// fetch all data for each run 
	async fetchChartData(metric) {
		// Show loading overlay
		this.setState({ 
			loading: true,
			showMetrics: false,
			showLoadingOverlay: true,
		});

		// deep clone run selections and fetch chart data for each run
		const chartRuns = structuredClone(this.props.pushSelectedRuns);
		let chartData = await HTTP.fetchChart(chartRuns, metric);

		// prevent app getting stuck on load if server fails to fetch
		if (chartData.length === 0) {
			this.setState({ 
				loading: false,
				showLoadingOverlay: false,
			});
			this.decrementPendingRenders(); // Decrement pending renders even if no data
			return;
		}

		// add the run data to the cloned run selections
		chartData.forEach(data => {	
			chartRuns.forEach(run => {
				if (run.name === data.name) {			
					if (run.data === undefined) {
						run.data = [];
					}
					run.data.push({
						timestamp: data.timestamp,
						value: data.value,
						step: data.step,
					});
				}
			});
		});

		// add the new chart data to the state with a unique ID based on unix timestamp
		let newCharts = [...this.state.charts];
		const chartId = Date.now();
		newCharts.push({ 
			id: chartId,
			data: chartRuns,
			metric: metric,
			context: {
				smoothing: 0,
				shownRuns: [],
				hiddenSeries: [],
				range: {min: 0, max: 0}
			}
		});

		// update chart state with new data
		this.setCharts(newCharts, true); // Pass `true` to indicate a chart render is complete

		// Hide loading overlay
		this.setState({ 
			loading: false,
			showLoadingOverlay: false,
		});
	}

	// add chart data to state for rendering 
	setCharts(newChartData, chartRendered = false) {

		this.setState({
			charts: newChartData,
			loading: false
		}, () => {
			// notify App about the active metrics
			if (this.props.updateChartMetrics) {
				const metrics = (newChartData || []).map(c => c.metric);
				this.props.updateChartMetrics(metrics);
			}
			if (chartRendered) {
				this.decrementPendingRenders();
			}

			// Open DataPicker if no charts are loaded
			if (newChartData.length === 0) {
				this.props.toggleDataPicker(true);
			}
		});

		// open data picker if no charts loaded
		if (newChartData.length === 0) {
			localStorage.removeItem("localCharts");
			//this.props.toggleDataPicker(true);	
		}
		else {
			// toggle data picker off if on
			this.props.toggleDataPicker(false);	

			// save data to local storage to persist through refreshes
			//storeChartDataInLocalStorage(newChartData);
		}
	}

	// Decrement the pending chart renders and check if all are done
	decrementPendingRenders() {
		this.setState(
			prevState => ({ pendingChartRenders: prevState.pendingChartRenders - 1 }),
			() => {
				this.checkAllChartsRendered();
			}
		);
	}

	// Check if all charts are rendered and call markUrlSyncComplete
	checkAllChartsRendered() {
		if (this.state.pendingChartRenders === 0 && this.props.markUrlSyncComplete) {
			this.props.markUrlSyncComplete(); // Notify App that sync is complete
		}
	}

	// removes chart from state using its id 
	removeChart(id) {
		let newCharts = [...this.state.charts].filter(chart => chart.id !== id);
		this.setCharts(newCharts); // setCharts will notify App
	}

	// update custom chart state for local data download
	syncData(id, newSmoothing, newShownRuns, newHiddenSeries, newRange) {
		const chartData = [...this.state.charts];
		chartData.forEach(chart => {
			if (chart.id === id) {
				chart.context.smoothing = newSmoothing;
				chart.context.shownRuns = newShownRuns;
				chart.context.hiddenSeries = newHiddenSeries;		
				chart.context.range = newRange;
			}
		})
		this.setState({
			charts: chartData,
		});
	}

	// upload data from a locally saved .json file
	async uploadLocalData() {

		const uploadedData = await new Promise((resolve) => {
			if (window.File && window.FileReader && window.FileList && window.Blob) {
				var file = document.querySelector('input[type=file]').files[0];
				var reader = new FileReader()
				var textFile = /json.*/;
				if (file.type.match(textFile)) {
					reader.onload = function (event) {
						try {
							const data = JSON.parse(event.target.result);
							//console.log(data); // debugging
							resolve(data);
						}
						catch {
							alert("Incompatible data file!");
						}			
					}
				} 
				else {
				   alert("Incompatible data file!");
				}
				reader.readAsText(file);
			} 
			else {
				alert("This browser is too old to support uploading data.");
			}
		});

		// clear file from hidden upload input
		this.inputField.current.value = "";
		
		const reversedUploadedData = uploadedData.reverse();

		// update with new id's so they are unique 
		for (let i = 0; i < reversedUploadedData.length; i++) {
			const chart = reversedUploadedData[i];
			chart.id = Date.now() + i;
		}
		const newChartData = [...this.state.charts, ...reversedUploadedData];

		// set uploaded data as new charts
		this.setCharts(newChartData);
	}

	// download data to a locally saved .json file
	downloadLocalData() {
		if (this.state.charts.length > 0) {
			const chartDataString = JSON.stringify(this.state.charts);
			const fileName = "data_" + new Date().toISOString() + ".json";
			const blob = new Blob([chartDataString], {type: 'text/plain'});
			if(window.navigator.msSaveOrOpenBlob) {
				window.navigator.msSaveBlob(blob, fileName);
			}
			else{
				const elem = window.document.createElement('a');
				elem.href = window.URL.createObjectURL(blob);
				elem.download = fileName;        
				document.body.appendChild(elem);
				elem.click();        
				document.body.removeChild(elem);
			}
			console.log("Downloading data... " + fileName);
		}
		else {
			alert("No chart data to download.")
		}	
	}

	render() {
		const { availableMetrics, charts, showLoadingOverlay } = this.state;

		// Group metrics by system name
		const groupedMetrics = availableMetrics.reduce((groups, metric) => {
			const match = metric.match(/^system\/([^-]+)-/);
			const groupName = match ? match[1] : "Other";
			if (!groups[groupName]) {
				groups[groupName] = [];
			}
			groups[groupName].push(metric);
			return groups;
		}, {});

		return (
			<div id="chartPickerWrapper">
				{/* Loading Overlay */}
				{showLoadingOverlay && (
					<div className="loadingOverlay">
						<div className="loadingSpinner"></div>
					</div>
				)}

				{/* Metric Sidebar */}
				<div id="metricSidebar">
					<div className="toggleDataPickerWrapper">
						<button 
							className="toggleDataPickerBtn" 
							onClick={() => this.props.toggleDataPicker(true)}
						>
							Data
						</button>
						<label className="upload">
							<input 
								type="file" 
								ref={this.inputField} 
								onChange={() => this.uploadLocalData()} 
							/>
							<img src={UploadIcon} className="uploadSVG" alt="Upload Charts" />
						</label>
						<button className="download" onClick={() => this.downloadLocalData()}>
							<img src={DownloadIcon} className="downloadSVG" alt="Download Charts" title="Download Charts" />
						</button>
					</div>
					<div id="metricBtnList">
						{Object.entries(groupedMetrics).map(([groupName, metrics]) => (
							<div key={groupName} className="metricGroup">
								<h4 className="metricGroupName">{groupName}</h4> {/* Group header */}
								{metrics.map(metric => {
									const displayName = metric.replace(/^system\/([^-]+)-/, ""); // Extract unmatched part
									return (
										<button
											key={metric}
											className="metricBtn"
											onClick={() => this.fetchChartData(metric)}
										>
											{displayName}
										</button>
									);
								})}
							</div>
						))}
						<div id="noData" className={availableMetrics.length === 0 ? null : "hide"}>
							No data available for current selection.
						</div>
					</div>
				</div>

				{/* Charts List */}
				<div id="chartsWrapper" className={this.props.className}>
					{charts.sort((a, b) => b.id - a.id).map(chart => (
						<Chart 
							key={chart.id} 
							chartData={chart}
							pullChartExtras={this.syncData.bind(this)}
							removeChart={this.removeChart.bind(this)}
						/>
					))}
				</div>		
			</div>
		);
	}
}


/* ChartPicker helper functions */
/*
function storeChartDataInLocalStorage(chartData) {
	let chartDataToBeLoaded = chartData.sort((a, b) => b.id - a.id);
	for (let i = 1; i < chartData.length + 1; i++) {	
		try {
			const newChartsString = JSON.stringify(chartDataToBeLoaded);
			localStorage.setItem("localCharts", newChartsString);
			break;
		}
		catch {

			chartDataToBeLoaded = chartData.slice(0, i * -1);
			continue;
		}
	}
}
*/
export default ChartPicker;
