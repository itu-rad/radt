import React from 'react'
import { HTTP } from '../api';
import '../styles/DataPicker.css';

class DataPicker extends React.Component {

	constructor(props) {
		super(props);
		this.state = {
			isFetching: false,
			experimentData: [],
			runData: [],
			activeExperimentId: null,
			activeWorkload: null,
			visibleWorkloads: [],
			visibleRuns: [],
			selectedWorkloads: [],
			selectedRuns: [],
		};

		this.bottomOfScrollRef = React.createRef();
	}

	componentDidMount() {

		// fetch data to populate pickers
		this.fetchExperiments();
		this.fetchRuns();

		// check if any selected runs are in local storage
		const localRunsAndWorkloadData = pullFromLocalStorage();
		if (localRunsAndWorkloadData.runData !== undefined && localRunsAndWorkloadData.workloadData !== undefined) {
			this.setState({
				selectedWorkloads: localRunsAndWorkloadData.workloadData,
				selectedRuns: localRunsAndWorkloadData.runData
			});
			this.props.pullSelectedRuns(localRunsAndWorkloadData.runData);
		}
	}

	componentDidUpdate(prevProps, prevState) {
		// scroll to bottom of list when adding selections, but not removing
		if (this.state.selectedWorkloads.length > prevState.selectedWorkloads.length) {
			this.bottomOfScrollRef.current.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'start' });
		}
	}

	// fetch all experiemnts 
	async fetchExperiments() {
		const data = await HTTP.fetchExperiments();
		this.setState({ experimentData: data });
	};

	// fetch all runs 
	async fetchRuns() {
		const data = await HTTP.fetchRuns();
		this.setState({ runData: data });
	};

	// select experiment and render its runs to the runs component 
	setVisibleWorkloads(experimentId) {
		const { runData } = this.state;
		let filteredRuns = [];
		for (let i = 0; i < runData.length; i++) {
			const run = runData[i];
			if (run.experimentId === experimentId) {
				filteredRuns.push(run);
			}
		}
		this.setState({
			visibleWorkloads: [], // removed conceptually
			visibleRuns: filteredRuns,
			activeExperimentId: experimentId,
			activeWorkload: null
		});
	}

	// adds or removes runs and workloads to a selection array 
	toggleRunWorkloadSelection(workload, run = null) {

		// grab current state and clone it for changes
		const { selectedWorkloads, selectedRuns, runData, experimentData } = this.state;
		let newSelectedWorkloads = [...selectedWorkloads];
		let newSelectedRuns = [...selectedRuns];

		// five different ways the user can add/remove data to selection
		if (workload === "null" && run === null) {
			newSelectedRuns.forEach(run => {
				if (run.workload.substring(run.workload.indexOf("-") + 1) === "null") {
					const runIndex = newSelectedRuns.findIndex(el => el.name === run.name);
					newSelectedRuns = newSelectedRuns.slice(0, runIndex).concat(newSelectedRuns.slice(runIndex + 1));
				}
			});
		}
		else if (run === null) {
			// add all runs from this workload to selection if they are not already added
			let workloadIndex = newSelectedWorkloads.indexOf(workload);
			if (workloadIndex === -1) {
				runData.forEach(run => {
					if (run.workload === workload) {
						let runIndex = newSelectedRuns.findIndex(el => el.name === run.name);
						if (runIndex === -1) {
							newSelectedRuns.push(run);
						}
					}
				});
			}
			else {
				// remove all runs from this workload from selection
				newSelectedRuns = newSelectedRuns.filter(el => el.workload !== workload);
			}
		}
		else {
			let runIndex = newSelectedRuns.findIndex(el => el.name === run.name);
			if (runIndex === -1) {
				// add run to selection if it is not already added
				newSelectedRuns.push(run);
			}
			else {
				// remove run from selection if it is already added
				newSelectedRuns = newSelectedRuns.slice(0, runIndex).concat(newSelectedRuns.slice(runIndex + 1));
			}
		}

		// update list of selected workloads based on new selected runs, and add experiment name to data 
		newSelectedWorkloads = [];
		newSelectedRuns.forEach(run => {
			let workloadIndex = newSelectedWorkloads.indexOf(run.workload);
			if (workloadIndex === -1) {
				newSelectedWorkloads.push(run.workload);
			}

			experimentData.forEach(experiment => {
				if (run.experimentId === experiment.id) {
					run.experimentName = experiment.name;
				}
			})
		});

		// update state
		this.setState({
			selectedWorkloads: newSelectedWorkloads,
			selectedRuns: newSelectedRuns
		});

		// pull copy of selected runs up to parent
		this.props.pullSelectedRuns(newSelectedRuns);

		// add selected runs and workloads to local storage to persist through refresh
		submitToLocalStorage(newSelectedWorkloads, newSelectedRuns);
	}

	// clear all selections
	clearAllSelections() {
		const confirm = window.confirm("Confirm clear all selections?");
		if (confirm) {
			// update state
			this.setState({
				selectedWorkloads: [],
				selectedRuns: []
			});

			// pull copy of selected runs up to parent
			this.props.pullSelectedRuns([]);

			// add selected runs and workloads to local storage to persist through refresh
			submitToLocalStorage([], []);
		}
	}

	render() {

		const {
			experimentData,
			visibleWorkloads,
			visibleRuns,
			activeExperimentId,
			activeWorkload,
			selectedWorkloads,
			selectedRuns,
		} = this.state;

		return (
			<div id="dataPickerWrapperBackground" className={this.props.toHide ? null : "hide"}>
				<div id="dataPickerWrapper">
					<Experiments
						data={experimentData}
						activeExperimentId={activeExperimentId}
						onClickSetVisibleWorkloads={this.setVisibleWorkloads.bind(this)}
					/>
					<Runs
						data={visibleRuns}
						selectedRuns={selectedRuns}
						onClickToggleRunSelection={this.toggleRunWorkloadSelection.bind(this)}
					/>
					<Selections
						selectedRuns={selectedRuns}
						onClickToggleWorkloadSelection={this.toggleRunWorkloadSelection.bind(this)}
						bottomOfScrollRef={this.bottomOfScrollRef}
					/>
					<button className="clearBtn" onClick={() => this.clearAllSelections()}>
						Clear All
					</button>
					<button className="selectionConfirmBtn" onClick={() => this.props.toggleDataPicker(false)}>
						Save
					</button>
				</div>
			</div>

		);

	}
}

/* DataPicker functional components */
function Experiments(props) {
	// simple search state
	const [query, setQuery] = React.useState('');

	const filtered = props.data
		.filter(e => (`${e.id} ${e.name}`).toLowerCase().includes(query.toLowerCase()))
		.sort((a, b) => a.id - b.id);

	return (
		<div id="experimentWrapper" style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
			<input
				className="searchInput"
				placeholder="Search experiments..."
				value={query}
				onChange={(e) => setQuery(e.target.value)}
			/>
			{/* scroll area must allow flex child to shrink: set minHeight:0 */}
			<div style={{ flex: 1, minHeight: 0, overflowY: 'auto' }}>
				<table className="pickerTable" style={{ width: '100%' }}>
					<thead>
						<tr>
							<th>ID</th>
							<th>Name</th>
						</tr>
					</thead>
					<tbody>
						{filtered.map(experiment => (
							<tr
								key={experiment.id}
								className={props.activeExperimentId === experiment.id ? "active" : null}
								onClick={() => props.onClickSetVisibleWorkloads(experiment.id)}
							>
								<td>{experiment.id}</td>
								<td>{experiment.name}</td>
							</tr>
						))}
					</tbody>
				</table>
			</div>
		</div>
	)
}
function Runs(props) {

	const checkRunStatus = (status) => {
		if (status === "FINISHED") {
			return "complete";
		}
		else if (status === "RUNNING") {
			return "running";
		}
		else if (status === "FAILED") {
			return "failed";
		}
	}

	// helper to format workload label (same logic previously used)
	function formatWorkloadLabel(workload) {
		const workloadId = workload.substring(workload.indexOf("-") + 1);
		if (workloadId === "null") {
			return "Unsorted";
		}
		return workloadId;
	}

	// same workload sort used elsewhere
	function sortWorkloads(a, b) {
		let x = a.substring(a.indexOf("-") + 1);
		let y = b.substring(b.indexOf("-") + 1);
		return x - y;
	}

	const [query, setQuery] = React.useState('');
	// first filter and sort runs
	const filteredRuns = props.data
		.slice()
		.sort((a, b) => b.startTime - a.startTime)
		.filter(run => {
			const q = query.toLowerCase();
			if (!q) return true;
			const paramsStr = Object.entries(run.params || {}).map(([k, v]) => `${k}:${v}`).join(' ');
			return (`${run.name} ${run.letter || ''} ${formatWorkloadLabel(run.workload)} ${paramsStr}`).toLowerCase().includes(q);
		});

	// group runs by workload preserving a sorted workload order
	const groupsMap = new Map();
	filteredRuns.forEach(run => {
		if (!groupsMap.has(run.workload)) groupsMap.set(run.workload, []);
		groupsMap.get(run.workload).push(run);
	});
	const groups = Array.from(groupsMap.entries()).sort((a, b) => sortWorkloads(a[0], b[0]));

	return (
		<div id="runsWrapper" style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
			<input
				className="searchInput"
				placeholder="Search runs..."
				value={query}
				onChange={(e) => setQuery(e.target.value)}
			/>
			<div style={{ flex: 1, minHeight: 0, overflowY: 'auto' }}>
				<table className="pickerTable" style={{ width: '100%' }}>
					<thead>
						<tr>
							<th>Status</th>
							<th>Identifier</th>
							<th>Workload</th>
							<th>Start</th>
							<th>Duration</th>
							<th>Info</th>
							<th style={{ width: '48px' }}>Select</th>
						</tr>
					</thead>
					<tbody>
						{groups.map(([workload, runs]) => {
							// determine if entire group is selected
							const groupSelected = runs.length > 0 && runs.every(r => props.selectedRuns.findIndex(el => el.name === r.name) > -1);
							return (
								<React.Fragment key={workload}>
									{/* group header row */}
									<tr className="groupHeader">
										<td colSpan="7">
											<div className="groupHeaderInner">
												<span className="groupLabel">{formatWorkloadLabel(workload) === "Unsorted" ? "Unsorted Runs" : "Workload " + formatWorkloadLabel(workload)}</span>
												<span
													className="groupCheckbox"
													onClick={(e) => { e.stopPropagation(); props.onClickToggleRunSelection(workload); }}
													title={groupSelected ? "Deselect workload" : "Select workload"}
												>
													{groupSelected ? "✔" : " "}
												</span>
											</div>
										</td>
									</tr>

									{/* runs for this workload */}
									{runs.map(run => (
										<tr
											key={run.name}
											onClick={() => props.onClickToggleRunSelection(run.workload, run)}
											className={props.selectedRuns.findIndex(el => el.name === run.name) > -1 ? "highlightSelection" : null}
										>
											<td title={run.status.charAt(0) + run.status.substring(1).toLowerCase()}>
												<span className={checkRunStatus(run.status)}>•</span>
											</td>
											<td className="letter" title="Identifier">{run.name.substring(0, 6) + " - " + (run.letter === null || run.letter === "0" ? "0" : run.letter)}</td>
											<td className="workloadId" title="Workload">{formatWorkloadLabel(run.workload)}</td>
											<td className="startTime" title="Start time">({howLongAgo(run.startTime)})</td>
											<td className={`duration ${run.duration === null ? "noDuration" : ""}`} title="Duration">{milliToMinsSecs(run.duration)}</td>
											<td className="info" title={Object.entries(run.params || {}).map(([k, v]) => `${k}: ${v}`).join('\n')}>i</td>
											<td style={{ textAlign: 'center' }}>
												<div
													className="checkbox"
													onClick={(e) => { e.stopPropagation(); props.onClickToggleRunSelection(run.workload, run); }}
												>
													{props.selectedRuns.findIndex(el => el.name === run.name) > -1 ? "✔" : " "}
												</div>
											</td>
										</tr>
									))}
								</React.Fragment>
							)
						})}
					</tbody>
				</table>
			</div>
		</div>
	)
}
function Selections(props) {
	// create new data object to render workloads and runs nicely in Selections
	let visibleSelection = [];
	props.selectedRuns.forEach(run => {

		let workload = run.workload;
		if (workload.substring(workload.indexOf("-") + 1) === "null") {
			workload = "null"
		}

		let workloadIndex = visibleSelection.findIndex(el => el.workload === workload);
		if (workloadIndex > -1) {
			let runIndex = visibleSelection[workloadIndex].runs.findIndex(el => el.name === run.name);
			if (runIndex === -1) {
				visibleSelection[workloadIndex].runs.push(run);
			}
		}
		else {
			let runs = [];
			runs.push(run);
			visibleSelection.push({
				workload: workload,
				runs: runs
			})
		}
	});

	function formatWorkloadLabel(workload) {
		if (workload === "null") {
			workload = "Unsorted Runs";
		}
		else {
			workload = "Workload " + workload;
		}
		return workload;
	}

	return (
		<div id="selectionsWrapper">
			{ /* render all workloads */}
			{visibleSelection.map(visibleWorkload => (
				<div
					className='workloadWrapper'
					key={visibleWorkload.workload}
				>
					<div className='workload'>
						<button
							className="removeWorkloadBtn"
							onClick={() => props.onClickToggleWorkloadSelection(visibleWorkload.workload)}
						>
							X
						</button>
						{formatWorkloadLabel(visibleWorkload.workload)}
					</div>
					<ul>
						{ /* render all runs */}
						{visibleWorkload.runs.sort((a, b) => a.startTime - b.startTime).map(visibleRun => (
							<li key={visibleRun.name}>
								{visibleRun.name.substring(0, 6) + " - " + (visibleRun.letter === null || visibleRun.letter === "0" ? "0" : visibleRun.letter)}

								<button
									className="removeBtn"
									onClick={() => props.onClickToggleWorkloadSelection(visibleWorkload.workload, visibleRun)}
								>
									X
								</button>
							</li>
						))}
					</ul>
				</div>
			))}
			{ /* div ref to scroll to bottom of */}
			<div ref={props.bottomOfScrollRef} />
		</div>
	);
}

/* DataPicker helper functions */

//from: https://stackoverflow.com/questions/9763441/milliseconds-to-time-in-javascript
function milliToMinsSecs(ms) {
  var s = (ms - ms % 1000) / 1000;
  var secs = s % 60;
  s = (s - secs) / 60;
  var mins = s % 60;
  s = (s - mins) / 60;
  var hrs = s % 24;
  var days = (s - hrs) / 24;

  // Pad to 2 or 3 digits, default is 2
  var pad = (n, z = 2) => ('00' + n).slice(-z);

  if (days === 1) {
	return days + ' day ' + pad(hrs) + ':' + pad(mins) + ':' + pad(secs);
  }
  if (days > 1) {
	return days + ' days ' + pad(hrs) + ':' + pad(mins) + ':' + pad(secs);
  }
  return pad(hrs) + ':' + pad(mins) + ':' + pad(secs);
}
function howLongAgo(startTime) {

	let howLongAgo;

	let diffTime = Date.now() - startTime;
	let years = diffTime / (365 * 24 * 60 * 60 * 1000);
	let months = diffTime / (30 * 24 * 60 * 60 * 1000);
	let days = diffTime / (24 * 60 * 60 * 1000);
	let hours = (days % 1) * 24;
	let minutes = (hours % 1) * 60;
	let secs = (minutes % 1) * 60;
	[years, months, days, hours, minutes, secs] = [Math.floor(years), Math.floor(months), Math.floor(days), Math.floor(hours), Math.floor(minutes), Math.floor(secs)];

	if (years > 0) {
		if (years === 1) {
			howLongAgo = years + " year ago";
		}
		else {
			howLongAgo = years + " years ago";
		}
	}
	else if (months > 0) {
		if (months === 1) {
			howLongAgo = months + " month ago";
		}
		else {
			howLongAgo = months + " months ago";
		}
	}
	else if (days > 0) {
		if (days === 1) {
			howLongAgo = days + " day ago";
		}
		else {
			howLongAgo = days + " days ago";
		}
	}
	else if (hours > 0) {
		if (hours === 1) {
			howLongAgo = hours + " hour ago";
		}
		else {
			howLongAgo = hours + " hours ago";
		}
	}
	else if (minutes > 0) {
		if (minutes === 1) {
			howLongAgo = minutes + " minute ago";
		}
		else {
			howLongAgo = minutes + " minutes ago";
		}
	}
	else if (secs > 0) {
		if (secs === 1) {
			howLongAgo = secs + " second ago";
		}
		else {
			howLongAgo = secs + " seconds ago";
		}
	}
	else {
		howLongAgo = "N/A";
	}

	return howLongAgo;
}
function submitToLocalStorage(workloadData, runData) {

	const workloadDataString = JSON.stringify(workloadData);
	localStorage.setItem("selectedWorkloads", workloadDataString);

	const runDataString = JSON.stringify(runData);
	localStorage.setItem("selectedRuns", runDataString);

	if (workloadData.length === 0 && runData.length === 0) {
		localStorage.removeItem("selectedWorkloads");
		localStorage.removeItem("selectedRuns");
	}
}
function pullFromLocalStorage() {

	// pull workload data (just for data picker)
	const workloadDataString = localStorage.getItem("selectedWorkloads");
	const workloadData = JSON.parse(workloadDataString);

	// pull run data (important stuff)
	const runDataString = localStorage.getItem("selectedRuns");
	const runData = JSON.parse(runDataString);

	// combine and return it
	const runsAndWorkloadData = [];
	if (runData !== null) {
		runsAndWorkloadData.runData = runData;
	}
	if (workloadData !== null) {
		runsAndWorkloadData.workloadData = workloadData;
	}
	return runsAndWorkloadData;
}

export default DataPicker;
