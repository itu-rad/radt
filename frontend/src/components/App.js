import React from 'react';
import DataPicker from './DataPicker';
import ChartPicker from './ChartPicker';
import '../styles/App.css';

class App extends React.Component {

	constructor(props) {
		super(props);
		this.state = {
			selectedRuns: [],
			dataPickerOpen: true
		};
	}

	// Update the URL to reflect the selected runs
	updateUrlWithSelectedRuns = (selectedRuns) => {
		const runIds = selectedRuns.map(run => run.name);
		const jsonRunIds = JSON.stringify(runIds); // Properly format as JSON array
		const newUrl = `${window.location.pathname}?runs=${jsonRunIds}`;
		window.history.replaceState(null, '', newUrl);
	}

	// Pull data from data picker when it updates
	updateSelectedRuns = (newSelectedRuns) => {
		this.setState({ selectedRuns: newSelectedRuns });
		this.updateUrlWithSelectedRuns(newSelectedRuns);
	}

	// Show or hide the data picker
	toggleDataPicker = (toShow) => {
		this.setState({ dataPickerOpen: toShow });
	}

	componentDidMount() {
		// Check URL for `runs` query parameter and overwrite selected runs
		const urlParams = new URLSearchParams(window.location.search);
		const runsParam = urlParams.get('runs');
		if (runsParam) {
			try {
				// Parse the `runs` parameter as a JSON array
				const runIds = JSON.parse(runsParam); // Ensure proper JSON parsing
				if (Array.isArray(runIds)) {
					this.setState({ selectedRuns: runIds.map(id => ({ name: id })) });
				} else {
					console.error("Invalid `runs` parameter format. Expected a JSON array.");
				}
			} catch (error) {
				console.error("Failed to parse `runs` parameter:", error);
			}
		}
	}

	render() {  
		const { selectedRuns, dataPickerOpen } = this.state;
		return (   
			<div id="appWrapper">
				<DataPicker 
					toHide={!dataPickerOpen}
					pullSelectedRuns={this.updateSelectedRuns} 
					toggleDataPicker={this.toggleDataPicker}
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
