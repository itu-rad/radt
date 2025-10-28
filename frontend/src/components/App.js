import React from 'react';
import DataPicker from './DataPicker';
import ChartPicker from './ChartPicker';
import '../styles/App.css';

class App extends React.Component {

	constructor(props) {
		super(props);
		this.state = {
			selectedRuns: [],
			chartMetrics: [],
			dataPickerOpen: false, // Start with DataPicker closed
			isSyncingFromUrl: true, // Flag to prevent premature URL updates
			urlSyncInProgress: true, // Flag to track URL sync progress
			isLoading: true // New state to track loading status
		};
	}

	// Update the URL to reflect the selected runs and charts
	updateUrlWithSelectedRuns = (selectedRuns, chartMetrics = null) => {
		if (this.state.urlSyncInProgress) return; // Prevent updates during sync
		const runIds = (selectedRuns || []).map(run => run.name);
		const charts = chartMetrics !== null ? chartMetrics : this.state.chartMetrics || [];
		const jsonRunIds = JSON.stringify(runIds);
		const jsonChartMetrics = JSON.stringify(charts);
		const newUrl = `${window.location.pathname}?runs=${jsonRunIds}&charts=${jsonChartMetrics}`;
		window.history.replaceState(null, '', newUrl);
	}

	// Pull data from data picker when it updates
	updateSelectedRuns = (newSelectedRuns) => {
		this.setState({ selectedRuns: newSelectedRuns }, () => {
			this.updateUrlWithSelectedRuns(this.state.selectedRuns);
		});
	}

	// Update chart metrics and URL
	updateChartMetrics = (newChartMetrics) => {
		this.setState({ chartMetrics: newChartMetrics || [] }, () => {
			this.updateUrlWithSelectedRuns(this.state.selectedRuns, this.state.chartMetrics);
		});
	}

	// Mark URL sync as complete
	markUrlSyncComplete = () => {
		this.setState({ urlSyncInProgress: false, isLoading: false }, () => {
			// Open DataPicker if no charts or runs are visible
			if (this.state.chartMetrics.length === 0 || this.state.selectedRuns.length === 0) {
				this.toggleDataPicker(true);
			}
			this.updateUrlWithSelectedRuns(this.state.selectedRuns, this.state.chartMetrics);
		});
	}

	// Show or hide the data picker
	toggleDataPicker = (toShow) => {
		this.setState({ dataPickerOpen: toShow });
	}

	render() {  
		const { selectedRuns, dataPickerOpen, isLoading } = this.state;
		return (   
			<div id="appWrapper">
				{isLoading && (
					<div className="loadingOverlay">
						<div className="loadingSpinner"></div>
					</div>
				)}
				<DataPicker 
					toHide={!dataPickerOpen}
					pullSelectedRuns={this.updateSelectedRuns}
					toggleDataPicker={this.toggleDataPicker}
					pushSelectedRuns={selectedRuns}
					markUrlSyncComplete={this.markUrlSyncComplete}
				/>
				<ChartPicker 
					toHide={dataPickerOpen}
					pushSelectedRuns={selectedRuns}
					toggleDataPicker={this.toggleDataPicker}
					updateChartMetrics={this.updateChartMetrics}
					markUrlSyncComplete={this.markUrlSyncComplete}
				/>
			</div>
		);
	}
}

export default App;
