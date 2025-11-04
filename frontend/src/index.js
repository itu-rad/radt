import React from 'react';
import ReactDOM from 'react-dom/client';
import Highcharts from 'highcharts/highstock';
import App from './components/App';

// import highcharts modules
require('highcharts/modules/boost')(Highcharts)
require('highcharts/indicators/indicators')(Highcharts)
require('highcharts/modules/exporting')(Highcharts)
require('highcharts/modules/offline-exporting')(Highcharts)
require('highcharts-export-data')(Highcharts)

const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(
	//<React.StrictMode>
	<App />
	//</React.StrictMode>
);

/* forces console clear on hot reload during development */
/*
window.addEventListener('message', e => {
	if (process.env.NODE_ENV !== 'production' && e.data && e.data.type === 'webpackInvalid') {
		console.clear();
	}
});
*/

// If you want to start measuring performance in your app, pass a function
// to log results (for example: reportWebVitals(console.log))
// or send to an analytics endpoint. Learn more: https://bit.ly/CRA-vitals
//reportWebVitals();

// new: iframe postMessage API
(function () {
	// adjust/extend allowed origins as needed
	const allowedParentOrigins = [
		'https://res72.itu.dk',
		'http://res72.itu.dk',
		'http://localhost:8000',
		'http://localhost:3000'
	];

	function isAllowedOrigin(origin) {
		// in production you should explicitly list allowed origins
		return true;
		return allowedParentOrigins.includes(origin);
	}

	async function handleFetchRequest(id, payload, source, origin) {
		try {
			// payload.url is relative or absolute; prefer relative path to your API
			const apiBase = process.env.REACT_APP_API_URL || '/api/';
			let fetchUrl = payload.url;
			if (!/^https?:\/\//.test(fetchUrl)) {
				// ensure single slash when concatenating
				const base = apiBase.endsWith('/') ? apiBase : apiBase + '/';
				fetchUrl = base + fetchUrl.replace(/^\/+/, '');
			}
			const resp = await fetch(fetchUrl, { headers: { 'Content-Type': 'application/json' } });
			const data = await resp.json().catch(() => null);
			source.postMessage({ type: 'fetchResponse', id, ok: resp.ok, status: resp.status, data }, origin);
		} catch (err) {
			source.postMessage({ type: 'fetchResponse', id, ok: false, error: String(err) }, origin);
		}
	}

	// window.addEventListener('message', (ev) => {
	// 	// DEBUG: show messages in dev
	// 	// if (process.env.NODE_ENV !== 'production') {
	// 	// 	console.debug('iframe received message', ev.origin, ev.data);
	// 	// }

	// 	if (!isAllowedOrigin(ev.origin)) {
	// 		// ignore messages from disallowed origins
	// 		if (process.env.NODE_ENV !== 'production') {
	// 			console.warn('Discarding message from disallowed origin', ev.origin);
	// 		}
	// 		return;
	// 	}

	// 	const msg = ev.data || {};
	// 	const source = ev.source;
	// 	const origin = ev.origin;

	// 	if (msg.type === 'getHref') {
	// 		// reply with current location.href
	// 		source.postMessage({ type: 'href', href: window.location.href }, origin);
	// 		return;
	// 	}

	// 	if (msg.type === 'fetch') {
	// 		// msg should include { id, url, options? }
	// 		const id = msg.id || null;
	// 		handleFetchRequest(id, msg.payload || {}, source, origin);
	// 		return;
	// 	}

	// 	// add more message handlers as needed (e.g., navigation, state queries)
	// });
})();
