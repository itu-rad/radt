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
			pendingChartRenders: 0,
			// keep showLoadingOverlay for backwards compat if other code uses it
			showLoadingOverlay: false,

			// NEW: multi-axis single-chart mode
			multiAxisMode: true,

			// NEW: version to force re-mount / re-render of combined chart
			combinedVersion: 0,

			// NEW: persisted context for the combined multi-axis chart
			combinedContext: {
				smoothing: 0,
				shownRuns: [],
				hiddenSeries: [],
				range: { min: 0, max: 0 }
			}
		};

		this.inputField = React.createRef();
		this._pendingChartsFromUrl = null;
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
				}else{
					this.props.markUrlSyncComplete();
				}
			} catch (e) {
				console.error("Failed to parse `charts` URL param:", e);
			}
		}else{
			this.props.markUrlSyncComplete();
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
				if (pending.length === 0) {
					// If no charts are pending, mark URL sync as complete
					this.props.markUrlSyncComplete();
				} else {
					// Use Promise.all to ensure all fetches complete
					for (const metric of pending) {
						await this.fetchChartData(metric);
					}
					// Check if all charts are rendered
					this.checkAllChartsRendered();
				}
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
		// Create a numeric placeholder id so sorting behaves the same
		const placeholderId = Date.now();

		// Insert placeholder chart object immediately
		this.setState(prev => ({
			charts: [
				...prev.charts,
				{
					id: placeholderId,
					metric,
					loading: true // signal placeholder
				}
			]
		}));

		try {
			// deep clone run selections and fetch chart data for each run
			const chartRuns = structuredClone(this.props.pushSelectedRuns || []);
			let chartData = await HTTP.fetchChart(chartRuns, metric);

			// handle empty/failed fetch: remove placeholder and decrement pending renders
			if (!chartData || chartData.length === 0) {
				this.setState(prev => ({
					charts: prev.charts.filter(c => c.id !== placeholderId)
				}));
				// only decrement if we are in URL sync mode
				if (this.state.pendingChartRenders > 0) this.decrementPendingRenders();
				return;
			}

			// add the run data to the cloned run selections (same logic as before)
			chartData.forEach(data => {
				chartRuns.forEach(run => {
					if (run.name === data.name) {
						if (run.data === undefined) run.data = [];
						run.data.push({
							timestamp: data.timestamp,
							value: data.value,
							step: data.step
						});
					}
				});
			});

			// build the final chart object (keep same id so it replaces placeholder)
			const finalChart = {
				id: placeholderId,
				data: chartRuns,
				metric: metric,
				context: {
					smoothing: 0,
					shownRuns: [],
					hiddenSeries: [],
					range: { min: 0, max: 0 }
				}
			};

			// --- REPLACED: use setCharts to update charts and bump combinedVersion ---
			// compute newCharts from current state (placeholder present) and replace placeholder with final chart
			const newCharts = (this.state.charts || []).map(c => (c.id === placeholderId ? finalChart : c));
			// pass chartRendered = true so setCharts will decrement pending renders for URL sync
			this.setCharts(newCharts, true);

			// notify App about active metrics (setCharts already does this, but keep as-safety)
			if (this.props.updateChartMetrics) {
				const metrics = (this.state.charts || []).map(c => c.metric);
				this.props.updateChartMetrics(metrics);
			}
		} catch (err) {
			// on error remove placeholder and decrement pending renders
			console.error("fetchChartData error:", err);
			this.setState(prev => ({
				charts: prev.charts.filter(c => c.id !== placeholderId)
			}), () => {
				if (this.state.pendingChartRenders > 0) this.decrementPendingRenders();
			});
		}
	}

	// add chart data to state for rendering 
	setCharts(newChartData, chartRendered = false) {

		this.setState(prev => ({
			charts: newChartData,
			loading: false,
			// bump combinedVersion so combined (multi-axis) Chart remounts/updates reliably
			combinedVersion: (prev.combinedVersion || 0) + 1
		}), () => {
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
		}
		else {
			// toggle data picker off if on
			this.props.toggleDataPicker(false);	
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
		// NEW: if multi-axis combined chart, persist context to combinedContext state
		if (id === 'multi-axis') {
			this.setState(prev => ({
				combinedContext: {
					smoothing: newSmoothing,
					shownRuns: newShownRuns,
					hiddenSeries: newHiddenSeries,
					range: newRange
				},
				// bump version so combined Chart gets remounted/updated predictably
				combinedVersion: (prev.combinedVersion || 0) + 1
			}), () => {
				// notify App about active metrics if needed (unchanged)
				if (this.props.updateChartMetrics) {
					const metrics = (this.state.charts || []).map(c => c.metric);
					this.props.updateChartMetrics(metrics);
				}
			});
			return;
		}

		// existing behaviour for single-chart sync
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

	// NEW: toggle multi-axis single-chart mode
	toggleMultiAxis = (toState = null) => {
		this.setState(prev => ({
			multiAxisMode: typeof toState === 'boolean' ? toState : !prev.multiAxisMode
		}), () => {
			// If leaving multi-axis mode, ensure App gets updated metrics as before
			if (!this.state.multiAxisMode && this.props.updateChartMetrics) {
				const metrics = (this.state.charts || []).map(c => c.metric);
				this.props.updateChartMetrics(metrics);
			}
		});
	}

	// NEW: toggle a metric on/off. If present, remove its chart(s); otherwise fetch it.
	toggleMetric = (metric) => {
		const { charts, multiAxisMode } = this.state;
		const existing = (charts || []).filter(c => c.metric === metric);

		if (existing.length > 0) {
			if (multiAxisMode) {
				// In multi-axis mode, update charts and bump combinedVersion in one setState
				this.setState(prev => {
					const newCharts = (prev.charts || []).filter(c => c.metric !== metric);
					return {
						charts: newCharts,
						combinedVersion: (prev.combinedVersion || 0) + 1
					};
				}, () => {
					// notify App about active metrics after update
					if (this.props.updateChartMetrics) {
						const metrics = (this.state.charts || []).map(c => c.metric);
						this.props.updateChartMetrics(metrics);
					}
				});
			} else {
				// Normal mode: remove via removeChart to preserve existing behavior
				existing.forEach(c => this.removeChart(c.id));
			}
		} else {
			// add/fetch chart for this metric
			this.fetchChartData(metric);
		}
	}

	render() {
		const { availableMetrics, charts, multiAxisMode, combinedVersion, combinedContext } = this.state;

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

		// NEW: build a combined chart object when multiAxisMode is enabled
		let combinedChart = null;
		// Only build combinedChart when multiAxisMode is enabled AND there are charts to combine
		if (multiAxisMode && (charts || []).length > 0) {
			// create a synthetic chart that contains each run+metric as a separate series
			const combinedSeries = [];
			(charts || []).forEach(chart => {
				// chart.data is an array of run objects with their own data arrays
				(chart.data || []).forEach(run => {
					// Normalize and validate datapoints; skip series with no valid points
					const pts = (run.data || [])
						.map(p => {
							// tolerate both {timestamp, value} and [t, v] shapes; coerce to numbers
							if (Array.isArray(p) && p.length >= 2) {
								return { timestamp: Number(p[0]), value: Number(p[1]) };
							}
							if (p && (p.timestamp !== undefined || p.time !== undefined)) {
								return { timestamp: Number(p.timestamp ?? p.time), value: Number(p.value) };
							}
							// invalid point -> drop
							return null;
						})
						.filter(Boolean)
						.filter(pt => Number.isFinite(pt.timestamp) && Number.isFinite(pt.value));

					if (pts.length === 0) return; // skip empty series

					combinedSeries.push({
						// unique name: run + metric so Chart can label series
						name: `${run.name} :: ${chart.metric}`,
						// carry basic run metadata through
						runName: run.run_name,
						experimentName: run.experimentName,
						workload: run.workload,
						// preserve original metric so Chart can label y-axis
						metric: chart.metric,
						// normalized datapoints
						data: pts
					});
				});
			});
			// only expose combinedChart if we actually have series to show
			if (combinedSeries.length > 0) {
				combinedChart = {
					id: 'multi-axis',
					metric: 'multi-axis',
					// Chart component expects a `data` field; provide series array
					data: combinedSeries,
					// USE persisted combinedContext from state so toggles persist and sync works
					context: combinedContext
				};
			}
		}

		return (
			<div id="chartPickerWrapper">
				{/* Metric Sidebar */}
				<div id="metricSidebar">
					<div className="toggleDataPickerWrapper">
						<button 
							className="toggleDataPickerBtn" 
							onClick={() => this.props.toggleDataPicker(true)}
						>
							Data
						</button>

						<button
							className={`multiAxisBtn ${multiAxisMode ? 'active' : ''}`}
							title="Toggle multi-axis single chart"
							onClick={() => this.toggleMultiAxis()}
							style={{
								marginLeft: 8,
								width: 140,
								height: 40,
								background: multiAxisMode ? '#115785' : '#fff',
								color: multiAxisMode ? '#fff' : '#115785',
								border: '1px solid #115785',
								cursor: 'pointer',
								fontWeight: '600'
							}}
						>
							{multiAxisMode ? 'Multi-Axis: ON' : 'Multi-Axis: OFF'}
						</button>

							{/* <label className="upload">
								<input 
									type="file" 
									ref={this.inputField} 
									onChange={() => this.uploadLocalData()} 
								/>
								<img src={UploadIcon} className="uploadSVG" alt="Upload Charts" />
							</label>
							<button className="download" onClick={() => this.downloadLocalData()}>
								<img src={DownloadIcon} className="downloadSVG" alt="Download Charts" title="Download Charts" />
							</button> */}
					</div>

					<div id="metricBtnList">
						{Object.entries(groupedMetrics).map(([groupName, metrics]) => (
							<div key={groupName} className="metricGroup">
								<h4 className="metricGroupName">{groupName}</h4> {/* Group header */}
								{metrics.map(metric => {
									const displayName = metric.replace(/^system\/([^-]+)-/, ""); // Extract unmatched part
									// NEW: determine if this metric is currently selected (has a chart)
									const isSelected = (charts || []).some(c => c.metric === metric);
									return (
										<button
											key={metric}
											className={`metricBtn ${isSelected ? 'selected' : ''}`}
											onClick={() => this.toggleMetric(metric)}
											title={isSelected ? `Remove ${displayName}` : `Show ${displayName}`}
										>
											{isSelected ? '✔ ' : ''}{displayName}
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
					{/* NEW: when in multi-axis mode render a single combined chart */}
					{multiAxisMode ? (
						combinedChart ? (
							<Chart
								// include combinedVersion in key so Chart remounts when charts array or combinedContext changes
								key={`${combinedChart.id}-${combinedVersion}`}
								chartData={combinedChart}
								pullChartExtras={this.syncData.bind(this)}
								removeChart={() => this.setCharts([])}
								// NEW: tell Chart to occupy full viewport height in multi-axis mode
								fullHeight={true}
							/>
						) : (
							<div style={{ padding: 20 }}>No chart data available to combine.</div>
						)
					) : (
						// ...existing behavior: render one Chart per chart item ...
						(charts || []).sort((a, b) => b.id - a.id).map(chart => {
							// Render an inline loading wrapper only for placeholders
							if (chart.loading) {
								return (
									<div key={chart.id} className="chartWrapper" style={{ height: '690px'}}>
										<div
											className="loadingOverlay"
											style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, backgroundColor: 'rgba(255,255,255,0.8)' }}
										>
											<div className="loadingSpinner" />
										</div>
									</div>
								);
							}
							// Render the Chart component directly (it provides its own wrapper)
							return (
								<Chart
									key={chart.id}
									chartData={chart}
									pullChartExtras={this.syncData.bind(this)}
									removeChart={this.removeChart.bind(this)}
								/>
							);
						})
					)}
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
