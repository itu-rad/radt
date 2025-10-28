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

	// pull data from data picker when it updates
	updateSelectedRuns = (newSelectedRuns) => {
		this.setState({selectedRuns: newSelectedRuns});
	}

	// show or hide the data picker
	toggleDataPicker = (toShow) => {
		this.setState({ dataPickerOpen: toShow });
	}

	render() {  
		const { selectedRuns, dataPickerOpen } = this.state;
		return (   
			<div id="appWrapper">
				{!dataPickerOpen && (
					<button 
						className="toggleDataPickerBtn" 
						onClick={() => this.toggleDataPicker(true)}
					>
						Data
					</button>
				)}
				<DataPicker 
					toHide={!dataPickerOpen}
					pullSelectedRuns={this.updateSelectedRuns} 
					toggleDataPicker={this.toggleDataPicker}
				/>
				<ChartPicker 
					toHide={dataPickerOpen}
					pushSelectedRuns={selectedRuns}
					toggleDataPicker={this.toggleDataPicker}
				/>
			</div>
		);
	}
}
export default App;
