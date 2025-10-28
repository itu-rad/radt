import React from 'react';
import DataPicker from './DataPicker';
import ChartPicker from './ChartPicker';
import '../styles/App.css';

class App extends React.Component {

	constructor(props) {
		super(props);
		this.state = {
			selectedRuns: [],
			chartMetrics: [], // added: currently selected chart metrics
			dataPickerOpen: true,
			isSyncingFromUrl: true // Flag to prevent premature URL updates
		};
	}

	// Update the URL to reflect the selected runs and charts
	updateUrlWithSelectedRuns = (selectedRuns, chartMetrics = null) => {
		// if (this.state.isSyncingFromUrl) return; // Prevent updating URL during initial sync
		const runIds = (selectedRuns || []).map(run => run.name);
		// prefer explicit chartMetrics param, otherwise use state
		const charts = chartMetrics !== null ? chartMetrics : this.state.chartMetrics || [];
		const jsonRunIds = JSON.stringify(runIds);
		const jsonChartMetrics = JSON.stringify(charts);
		const newUrl = `${window.location.pathname}?runs=${jsonRunIds}&charts=${jsonChartMetrics}`;
		window.history.replaceState(null, '', newUrl);
	}

	// Pull data from data picker when it updates
	updateSelectedRuns = (newSelectedRuns) => {
		this.setState({ selectedRuns: newSelectedRuns }, () => {
			// Ensure URL is updated after state change (preserve current charts)
			this.updateUrlWithSelectedRuns(this.state.selectedRuns);
		});
	}

	// added: update chart metrics and update URL
	updateChartMetrics = (newChartMetrics) => {
		this.setState({ chartMetrics: newChartMetrics || [] }, () => {
			this.updateUrlWithSelectedRuns(this.state.selectedRuns, this.state.chartMetrics);
		});
	}

	// Show or hide the data picker
	toggleDataPicker = (toShow) => {
		this.setState({ dataPickerOpen: toShow });
	}

	render() {  
		const { selectedRuns, dataPickerOpen } = this.state;
		return (   
			<div id="appWrapper">
				<DataPicker 
					toHide={!dataPickerOpen}
					pullSelectedRuns={this.updateSelectedRuns}
					toggleDataPicker={this.toggleDataPicker}
					pushSelectedRuns={selectedRuns}
				/>
				<ChartPicker 
					toHide={dataPickerOpen}
					pushSelectedRuns={selectedRuns}
					toggleDataPicker={this.toggleDataPicker}
					updateChartMetrics={this.updateChartMetrics} // new prop
					className={dataPickerOpen ? "blurred" : ""} // Pass blurred class
				/>
			</div>
		);
	}
}

export default App;
