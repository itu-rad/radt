import React from 'react'
import { HTTP } from '../api';
import '../styles/DataPicker.css';
import 'antd/dist/antd.css'; // added
import { Table, Input } from 'antd'; // added
import { CheckCircleOutlined, ClockCircleOutlined, CloseCircleOutlined } from '@ant-design/icons'; // <-- added icons

const RUNS_COLOR_PALETTE = [
  '#a6630c', // Brown
  '#c83243', // Coral
  '#b45091', // Pink
  '#8a63bf', // Purple
  '#434a93', // Indigo
  '#137dae', // Turquoise
  '#04867d', // Teal
  '#308613', // Lime
  '#facb66', // Lemon

  // Colors list, intensities 700-400:
  '#1f272d', // Grey 700
  '#445461', // Grey 600
  '#5f7281', // Grey 500
  '#8396a5', // Grey 400

  '#93320b', // Yellow 700
  '#be501e', // Yellow 600
  '#de7921', // Yellow 500
  '#f2be88', // Yellow 400

  '#115026', // Green 700
  '#277c43', // Green 600
  '#3caa60', // Green 500
  '#8ddda8', // Green 400

  '#9e102c', // Red 700
  '#c82d4c', // Red 600
  '#e65b77', // Red 500
  '#f792a6', // Red 400

  '#0e538b', // Blue 700
  '#2272b4', // Blue 600
  '#4299e0', // Blue 500
  '#8acaff', // Blue 400
];

// based on: https://github.com/mlflow/mlflow/blob/31bec963dbcc2e164e4614b756e8d0e401548659/mlflow/server/js/src/experiment-tracking/utils/RunNameUtils.ts#L4
function getStableColorIndex(runUuid) {
  let a = 0,
    b = 0;

  // Let's use super simple hashing method
  for (let i = 0; i < runUuid.length; i++) {
    a = (a + runUuid.charCodeAt(i)) % 255;
    b = (b + a) % 255;
  }

  // eslint-disable-next-line no-bitwise
  return RUNS_COLOR_PALETTE[(a | (b << 8)) % RUNS_COLOR_PALETTE.length];
}

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
			setSelectedRuns: (newSelectedRuns) => {
				this.setState({ selectedRuns: newSelectedRuns });
				this.props.pullSelectedRuns(newSelectedRuns);
				submitToLocalStorage(this.state.selectedWorkloads, newSelectedRuns);
			}
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
						setSelectedRuns={this.state.setSelectedRuns}
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
		.sort((a, b) => a.id - b.id)
		.map(e => ({ ...e, key: e.id }));

	const columns = [
		{ title: 'ID', dataIndex: 'id', key: 'id', width: 80 },
		{ title: 'Name', dataIndex: 'name', key: 'name' }
	];

	return (
		<div id="experimentWrapper" style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
			<Input
				className="searchInput"
				placeholder="Search experiments..."
				value={query}
				onChange={(e) => setQuery(e.target.value)}
			/>
			{/* scroll area must allow flex child to shrink: set minHeight:0 */}
			<div style={{ flex: 1, minHeight: 0, overflowY: 'auto' }}>
				<Table
					columns={columns}
					dataSource={filtered}
					pagination={false}
					rowClassName={(record) => props.activeExperimentId === record.id ? "active" : ""}
					onRow={(record) => ({
						onClick: () => props.onClickSetVisibleWorkloads(record.id)
					})}
					size="small"
				/>
			</div>
		</div>
	)
}


// add helper to convert hex color to rgba string with alpha
function hexToRgba(hex, alpha = 1) {
	// normalize and parse hex (supports #rgb and #rrggbb)
	const h = hex.replace('#', '');
	const full = h.length === 3 ? h.split('').map(c => c + c).join('') : h;
	const bigint = parseInt(full, 16);
	const r = (bigint >> 16) & 255;
	const g = (bigint >> 8) & 255;
	const b = bigint & 255;
	return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function Runs(props) {
	const [query, setQuery] = React.useState('');

	// Filter runs based on the search query
	const filteredRuns = props.data
		.slice()
		.sort((a, b) => (b.startTime || 0) - (a.startTime || 0))
		.filter(run => {
			const q = query.toLowerCase();
			if (!q) return true;
			const paramsStr = Object.entries(run.params || {}).map(([k, v]) => `${k}:${v}`).join(' ');
			return (`${run.name || ''} ${run.run_name || ''} ${run.letter || ''} ${run.workload || ''} ${paramsStr}`).toLowerCase().includes(q);
		})
		.map(run => ({ ...run, key: run.name }));

	const columns = [
		{
			title: '',
			dataIndex: 'status',
			key: 'statusStart',
			width: 180,
			render: (_, record) => {
				const status = record.status;
				const start = record.startTime;
				let IconComponent = ClockCircleOutlined;
				let cls = 'running';
				if (status === "FINISHED") { IconComponent = CheckCircleOutlined; cls = 'complete'; }
				else if (status === "FAILED") { IconComponent = CloseCircleOutlined; cls = 'failed'; }

				return (
					<div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
						<IconComponent className={cls} style={{ fontSize: 18 }} />
						<span className="startTime" title={new Date(start).toString()}>{`(${howLongAgo(start)})`}</span>
					</div>
				);
			}
		},
		{
			title: 'Identifier',
			dataIndex: 'run_name',
			key: 'run_name',
			render: (run_name, record) => run_name || (record.name ? record.name.substring(0, 6) : '')
		},
		{
			title: 'Workload',
			dataIndex: 'workload',
			key: 'workload',
			render: (workload) => workload || 'N/A'
		},
		{
			title: 'Duration',
			dataIndex: 'duration',
			key: 'duration',
			render: (duration) => <span className={duration === null ? "noDuration" : ""}>{milliToMinsSecs(duration)}</span>
		},
		{
			title: 'Info',
			dataIndex: 'params',
			key: 'params',
			render: (params) => <span className="info" title={Object.entries(params || {}).map(([k, v]) => `${k}: ${v}`).join('\n')}>i</span>
		},
		{
			title: 'Select',
			key: 'select',
			width: 70,
			render: (_, record) => {
				const isSelected = props.selectedRuns.findIndex(el => el.name === record.name) > -1;
				return (
					<div
						className="checkbox"
						onClick={(e) => {
							e.stopPropagation();
							const newSelectedRuns = isSelected
								? props.selectedRuns.filter(run => run.name !== record.name)
								: [...props.selectedRuns, record];
							props.setSelectedRuns(newSelectedRuns);
						}}
					>
						{isSelected ? "✔" : " "}
					</div>
				);
			}
		}
	];

	return (
		<div id="runsWrapper" style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
			<Input
				className="searchInput"
				placeholder="Search runs..."
				value={query}
				onChange={(e) => setQuery(e.target.value)}
			/>
			<div style={{ flex: 1, minHeight: 0, overflowY: 'auto' }}>
				<Table
					columns={columns}
					dataSource={filteredRuns}
					pagination={false}
					rowClassName={(record) => props.selectedRuns.findIndex(el => el.name === record.name) > -1 ? "highlightSelection" : ""}
					onRow={(record) => {
						const selected = props.selectedRuns.findIndex(el => el.name === record.name) > -1;
						const baseColor = getStableColorIndex(record.name);
						const unselectedBg = hexToRgba(baseColor, 0.12);
						const selectedBg = hexToRgba(baseColor, 0.24);
						const style = selected
							? { '--selected-border': baseColor, '--selected-bg': selectedBg }
							: { backgroundColor: unselectedBg };
						return {
							onClick: () => props.onClickToggleRunSelection(record.workload, record),
							style
						};
					}}
					size="small"
				/>
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
// unified duration helpers

function _computeUnits(ms) {
	if (ms == null || !isFinite(ms)) return null;
	const secs = ms / 1000;
	const mins = secs / 60;
	const hrs = mins / 60;
	const days = hrs / 24;
	const months = days / 30; // approximate
	const years = days / 365; // approximate
	return { years, months, days, hrs, mins, secs };
}

function _chooseLargestUnit(ms) {
	const u = _computeUnits(ms);
	if (!u) return null;
	if (u.years >= 1) return { value: u.years, shortUnit: 'yrs', longUnit: 'year' };
	if (u.months >= 1) return { value: u.months, shortUnit: 'mos', longUnit: 'month' };
	if (u.days >= 1) return { value: u.days, shortUnit: 'days', longUnit: 'day' };
	if (u.hrs >= 1) return { value: u.hrs, shortUnit: 'hrs', longUnit: 'hour' };
	if (u.mins >= 1) return { value: u.mins, shortUnit: 'mins', longUnit: 'minute' };
	return { value: Math.max(u.secs, 0), shortUnit: 'secs', longUnit: 'second' };
}

function milliToMinsSecs(ms) {
	// keep original behavior for invalid values
	if (ms == null || !isFinite(ms)) return "N/A";

	const unit = _chooseLargestUnit(ms);
	if (!unit) return "N/A";

	// original displayed fractional values with one decimal
	return `${unit.value.toFixed(1)} ${unit.shortUnit}`;
}

function howLongAgo(startTime) {
	// guard invalid start times
	if (!startTime || !isFinite(startTime)) return "N/A";

	const diffTime = Date.now() - startTime;
	if (diffTime <= 0) return "N/A";

	const unit = _chooseLargestUnit(diffTime);
	if (!unit) return "N/A";

	// original behavior floored all values and returned N/A for zero
	const intVal = Math.floor(unit.value);
	if (intVal <= 0) return "N/A";

	const unitLabel = intVal === 1 ? unit.longUnit : unit.longUnit + "s";
	return `${intVal} ${unitLabel} ago`;
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
