import React from 'react';
import DataPicker from './DataPicker';
import ChartPicker from './ChartPicker';
import '../styles/App.css';

class App extends React.Component {

	constructor(props) {
		super(props);
		this.state = {
			selectedRuns: [],
			dataPickerOpen: true,
			isSyncingFromUrl: true // Flag to prevent premature URL updates
		};
	}

	// Update the URL to reflect the selected runs
	updateUrlWithSelectedRuns = (selectedRuns) => {
		if (this.state.isSyncingFromUrl) return; // Prevent updating URL during initial sync
		const runIds = selectedRuns.map(run => run.name);
		const jsonRunIds = JSON.stringify(runIds); // Properly format as JSON array
		const newUrl = `${window.location.pathname}?runs=${jsonRunIds}`;
		window.history.replaceState(null, '', newUrl);
	}

	// Pull data from data picker when it updates
	updateSelectedRuns = (newSelectedRuns) => {
		this.setState({ selectedRuns: newSelectedRuns }, () => {
			// Ensure URL is updated after state change
			this.updateUrlWithSelectedRuns(this.state.selectedRuns);
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
					className={dataPickerOpen ? "blurred" : ""} // Pass blurred class
				/>
			</div>
		);
	}
}

export default App;
