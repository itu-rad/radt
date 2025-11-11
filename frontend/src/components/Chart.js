import React from 'react';
import { useState, useEffect, useRef } from 'react';
import ReactECharts from 'echarts-for-react';
import * as echarts from 'echarts';
import '../styles/Chart.css';

const COMMON_TOOLBOX = {
    show: true,
    feature: {
        dataZoom: { show: true},
        restore: { show: true },
        myDummy1: {
            show: true,
            title: '',
            // invisible rectangle path: occupies space but will be rendered transparent via iconStyle
            icon: 'path://M0 0 L24 0 L24 24 L0 24 Z',
            iconStyle: { opacity: 0, cursor: 'default', pointerEvents: 'none' },
            emphasis: { iconStyle: { opacity: 0, cursor: 'default', pointerEvents: 'none' } },
            onclick: () => {}
        },
        magicType: { show: true, type: ['line', 'bar', 'stack'] },
        saveAsImage: { show: true, pixelRatio: 10},
        // custom dummy feature inserted BEFORE myRemove
        myDummy2: {
            show: true,
            title: '',
            // invisible rectangle path: occupies space but will be rendered transparent via iconStyle
            icon: 'path://M0 0 L24 0 L24 24 L0 24 Z',
            iconStyle: { opacity: 0, cursor: 'default', pointerEvents: 'none' },
            emphasis: { iconStyle: { opacity: 0, cursor: 'default', pointerEvents: 'none' } },
            onclick: () => {}
        },
        // custom remove feature (icon/title defined here; onclick attached per-chart below)
        myRemove: {
            show: true,
            title: 'Remove Chart',
            // simple "X" path icon
            icon: 'path://M4 4 L12 12 M12 4 L4 12'
        }
    },
};

class Chart extends React.Component {
	constructor(props) {
		super(props);
		this.state = {
			options: {
				title: { text: '', left: 'center', textStyle: { fontWeight: 'bold' } },
				tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
				// place legend at bottom (px) so it anchors consistently across resolutions
				legend: { data: [], bottom: 48 },
				xAxis: { type: 'time', name: 'Time Elapsed', axisLabel: { formatter: (val) => milliToMinsSecs(val) } },
				yAxis: [{ type: 'value', name: 'Value', nameLocation: 'middle', nameGap: 50 }],
				series: [],
				// reserve bottom space for legend + slider and keep slider below legend
				grid: { left: 60, right: 60, top: 60, bottom: 96 },
				// slider bottom should be less than legend.bottom so it sits below it
				dataZoom: [{ type: 'inside', xAxisIndex: [0] }, { type: 'slider', xAxisIndex: [0], bottom: 12 }],
				toolbox: COMMON_TOOLBOX // unified toolbox
			},
             loading: true,
             id: null,
             data: [],
             workloads: [],
             shownRuns: [],
             hiddenSeries: [],
             smoothing: 0,
             range: {min: 0, max: 0},
             monochromeMode: false,
             // NEW: SVG renderer toggle (off by default) and version to force remount
             svgRendererEnabled: false,
             rendererVersion: 0,
             boostMode: true,
             chartLineWidth: 3.0,
             wrapperReady: false, // wait until wrapper has layout before rendering chart
		}; 

        this.chartRef = React.createRef();
        this.wrapperRef = React.createRef();
        this.handleShowRunsSwitch = (workloadId) => (event) => this.toggleShownRuns(workloadId, event);
        this._resizeObserver = null;
		// NEW: flag to suppress parent-sync calls while reacting to incoming props
		this._skipPullAfterPropChange = false;

        // reference to echarts instance (set in afterChartCreated)
        this.ecInstance = null;
	}

    // called when echarts instance is ready
    afterChartCreated = (ec) => {
        this.ecInstance = ec;
        // If we already have options in state (initial mount), apply them directly to the instance
        try {
            if (this.state && this.state.options) {
                ec.setOption(this.state.options, true);
            }
            // force a resize on next tick so echarts calculates correct size
            setTimeout(() => { try { ec.resize(); } catch (e) {} }, 0);
        } catch (e) {}
    }

    componentDidMount() {
        // only show workload ID's which contain more than 1 run in Toggle Run section
        const workloads = this.props.chartData.data.map(run => run.workload);
        const nonUnique = [...new Set(workloads.filter((item,i) => workloads.includes(item, i+1)))];
        this.setState({workloads: nonUnique});

        // generate series
        if (this.props.chartData.context) {
            // with local uploaded settings 
            this.generateSeries(this.props.chartData, this.props.chartData.context.smoothing, this.props.chartData.context.shownRuns, this.props.chartData.context.hiddenSeries, this.props.chartData.context.range, this.state.monochromeMode); 
        }   
        else {
            // with default settings (e.g. no smoothing and combined as workloads)
            this.generateSeries(this.props.chartData, 0, [], [], {min: 0, max: 0}, false); 
        }

        // Defer mounting the chart until the wrapper is laid out to avoid wrong initial render.
        const el = this.wrapperRef.current;
        if (el) {
            if (typeof ResizeObserver !== 'undefined') {
                this._resizeObserver = new ResizeObserver(entries => {
                    for (const entry of entries) {
                        const { width, height } = entry.contentRect || {};
                        if (width > 0 && height > 0) {
                            this.setState({ wrapperReady: true }, () => {
                                if (this._resizeObserver) { this._resizeObserver.disconnect(); this._resizeObserver = null; }
                            });
                            return;
                        }
                    }
                });
                this._resizeObserver.observe(el);
            } else {
                // fallback: poll until sized
                const checkSize = () => {
                    const r = el.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0) {
                        this.setState({ wrapperReady: true });
                    } else {
                        requestAnimationFrame(checkSize);
                    }
                };
                requestAnimationFrame(checkSize);
            }
        } else {
            // if no ref (unlikely), allow chart to render
            this.setState({ wrapperReady: true });
        }
    }

    componentWillUnmount() {
        if (this._resizeObserver) {
            try { this._resizeObserver.disconnect(); } catch (e) {}
            this._resizeObserver = null;
        }
    }
 
    componentDidUpdate(prevProps, prevState) {
		// If chartData prop changed (new combined chart or a different single chart), regenerate series
		if (prevProps.chartData !== this.props.chartData) {
			// recompute workloads (keep same logic as componentDidMount)
			const workloads = (this.props.chartData && this.props.chartData.data) ? this.props.chartData.data.map(run => run.workload) : [];
			const nonUnique = [...new Set(workloads.filter((item,i) => workloads.includes(item, i+1)))];

			// Determine context values from incoming chartData (fallback to defaults)
			const ctx = this.props.chartData && this.props.chartData.context ? this.props.chartData.context : null;
			const smoothing = ctx ? ctx.smoothing : 0;
			const shownRuns = ctx ? ctx.shownRuns : [];
			const hiddenSeries = ctx ? ctx.hiddenSeries : [];
			const range = ctx ? ctx.range : { min: 0, max: 0 };

			// Set flag to avoid pushing the (just-applied) state back to parent
			this._skipPullAfterPropChange = true;

			// Update workloads then regenerate series with preserved monochromeMode
			this.setState({ workloads: nonUnique }, () => {
				this.generateSeries(this.props.chartData, smoothing, shownRuns, hiddenSeries, range, this.state.monochromeMode);
				// Clear the skip flag on the next tick so subsequent user interactions still call pullChartExtras
				setTimeout(() => { this._skipPullAfterPropChange = false; }, 0);
			});
		}

		// Existing behavior: notify parent when chart-level state changes, but only if not suppressing due to prop-driven update
		if (
			(prevState.smoothing !== this.state.smoothing ||
			prevState.shownRuns.length !== this.state.shownRuns.length ||
			prevState.hiddenSeries.length !== this.state.hiddenSeries.length ||
			prevState.range.min !== this.state.range.min ||
			prevState.range.max !== this.state.range.max)
			&& !this._skipPullAfterPropChange
		) {
			this.props.pullChartExtras(this.state.id, this.state.smoothing, this.state.shownRuns, this.state.hiddenSeries, this.state.range);
		}
	}

    // format detailed tooltip if it is enabled
    static formatTooltip(tooltip, toShow) {

        const xAxisTitle = tooltip.series.xAxis.axisTitle.textStr;
        const yAxisTitle = tooltip.series.yAxis.axisTitle.textStr;

        let tooltipData = "<div class='tooltipStyle' style='color:" + tooltip.color + ";'>" + tooltip.series.name + "</div><br /><br/><b>" + yAxisTitle + ":</b> " + tooltip.y + "<br/><b>"+ xAxisTitle +":</b> " + milliToMinsSecs(tooltip.x) + "<br /><br />";

        if (toShow) {
            const letters = new Set();
            const models = new Set();
            const sources = new Set();
            const params = new Set();
            tooltip.series.userOptions.custom.runs.forEach(run => {  
                letters.add(run.letter);
                models.add(run.model);
                sources.add(run.source);
                params.add(run.params);
            });

            let lettersString = "";
            if (tooltip.series.userOptions.custom.runs.length > 1) {
                lettersString = "<b>Run(s): </b><br />" + [...letters].join("<br />") + "<br /><br />";
            }

            const modelsString = "<b>Model(s): </b><br />" + [...models].join("<br />") + "<br /><br />";
            const sourcesString = "<b>Source(s): </b><br />" + [...sources].join("<br />") + "<br /><br />";
            const paramsString = "<b>Param(s): </b><br />" + [...params].join("<br />") + "<br /><br />";

            tooltipData = tooltipData + modelsString + sourcesString + paramsString + lettersString;
        }
        
        return tooltipData;
    }
    

    // takes the run data, parses it to an object Highcharts can render, and applies it to state (which will auto-update the chart)
    generateSeries(newChartData, newSmoothing, newShownRuns, newHiddenSeries, newRange, monoMode) {
        // prepare a toolbox for this chart instance that wires the remove action to the parent callback
        const toolboxWithRemove = {
            ...COMMON_TOOLBOX,
            feature: {
                ...COMMON_TOOLBOX.feature,
                myRemove: {
                    ...COMMON_TOOLBOX.feature.myRemove,
                    onclick: () => {
                        try {
                            if (this.props && typeof this.props.removeChart === 'function') {
                                this.props.removeChart(newChartData && newChartData.id);
                            }
                        } catch (e) { /* swallow */ }
                    }
                }
            }
        };

        if (!newChartData || !newChartData.data) {
            const newOpts = { ...this.state.options, series: [] };
            if (this.ecInstance) {
                try { this.ecInstance.setOption(newOpts, true); } catch (e) {}
                setTimeout(() => { try { this.ecInstance.resize(); } catch (e) {} }, 0);
                // update non-options state only
                this.setState({ loading: false });
            } else {
                // echarts instance not ready -> set options in state so ReactECharts mounts with them
                this.setState({ loading: false, options: newOpts });
            }
             return;
        }

        // palettes and helpers
        const monoDashStyles = ['solid', 'dashed', 'dotted', 'dashed'];
        const monoColors = ['#000000', '#cccccc', '#7f7f7f', '#999999', '#666666'];
        const plotColors = ['#0070DE', '#ABD473', '#9482C9', '#C41F3B', '#00FF96', '#A330C9', '#F58CBA', '#FF7D0A', '#C79C6E', '#FF4D6B', '#69CCF0', '#FFD100'];
        // dash styles used to indicate different metrics (dash chosen by metricIndex)
        const dashTypes = ['solid', 'dashed', 'dotted'];

        // multi-axis mode detection
        if (newChartData.metric === 'multi-axis') {
            // newChartData.data is array of run-level objects with .metric
            const metricsMap = new Map(); // metric -> Map(workloadId -> seriesObj)
            newChartData.data.forEach(run => {
                if (!run.data || run.data.length === 0) return;
                const metric = run.metric || 'unknown';
                if (!metricsMap.has(metric)) metricsMap.set(metric, new Map());
                const workloadMap = metricsMap.get(metric);

                const rawWorkload = run.workload || '';
                let workloadId = rawWorkload;
                const letter = (typeof run.letter === 'string' && run.letter.length > 0) ? run.letter : null;
                const runIdForLabel = run.runName || run.name || '';
                const dashPos = rawWorkload.indexOf('-');
                const tail = dashPos >= 0 ? rawWorkload.substring(dashPos + 1) : '';

                if (tail === 'null' || (newShownRuns && newShownRuns.indexOf(rawWorkload) > -1)) {
                    if (!letter) {
                        if (dashPos >= 0) {
                            const removeNull = rawWorkload.substring(0, dashPos);
                            workloadId = removeNull + ' (' + (runIdForLabel ? runIdForLabel.substring(0, 5) : '') + ')';
                        } else {
                            workloadId = rawWorkload + ' (' + (runIdForLabel ? runIdForLabel.substring(0, 5) : '') + ')';
                        }
                    } else {
                        if (letter.length > 1) {
                            workloadId = rawWorkload + ' ' + letter;
                        } else {
                            workloadId = rawWorkload + ' ' + letter + ' (' + (runIdForLabel ? runIdForLabel.substring(0, 5) : '') + ')';
                        }
                    }
                }

                if (!workloadMap.has(workloadId)) {
                    // keep runs metadata on the "custom" holder (used to populate point-level runs below)
                    workloadMap.set(workloadId, { id: workloadId, data: [], custom: { runs: [] }, metric, rawWorkload });
                }
                const sObj = workloadMap.get(workloadId);
                const perRunData = (run.data || []).map(pt => [pt.timestamp, pt.value]);
                // include metric on run metadata as well so it is available when attached to points
                const metadata = {
                    name: run.runName || run.name,
                    id: run.name,
                    experimentName: run.experimentName,
                    workload: rawWorkload,
                    letter: run.letter ?? null,
                    model: run.model ?? undefined,
                    source: run.source ?? undefined,
                    params: run.params ?? undefined,
                    metric: metric,
                    data: perRunData
                };
                sObj.custom.runs.push(metadata);
                perRunData.forEach(([t, v]) => sObj.data.push([t, v])); // keep flat numeric storage for now
            });

            // build echarts yAxis and series
            const echYAxes = [];
            const echSeries = [];
            // lightweight lookup mapping seriesName -> { runs: [...], metric: '...' }
            const seriesMetaLookup = {};
            let metricIndex = 0;
            for (const [metric, workloadMap] of metricsMap.entries()) {
                echYAxes.push({ type: 'value', name: metric, position: metricIndex % 2 === 1 ? 'right' : 'left', offset: metricIndex * 50, nameLocation: 'middle', nameGap: 50});
                let seriesIdx = 0;
                for (const [workloadId, s] of workloadMap.entries()) {
                    if (!s.data || s.data.length === 0) continue;
                    const showRunsIndividually = newShownRuns && newShownRuns.indexOf(s.rawWorkload) > -1;
                    if (showRunsIndividually) {
                        s.custom.runs.forEach((runMeta) => {
                            if (!runMeta.data || runMeta.data.length === 0) return;
                            runMeta.data.sort((a, b) => a[0] - b[0]);
                            const earliest = runMeta.data[0][0];
                            // keep points compact: simple [time-offset, value] arrays
                            const points = runMeta.data.map(pt => [pt[0] - earliest, pt[1]]);
                            const seriesName = runMeta.name || runMeta.id;
                            // populate lookup once per series and record workload on meta
                            seriesMetaLookup[seriesName] = seriesMetaLookup[seriesName] || { runs: [], metric, workload: runMeta.workload || s.rawWorkload };
                            seriesMetaLookup[seriesName].runs.push(runMeta);
                            seriesMetaLookup[seriesName].metric = metric;
                            seriesMetaLookup[seriesName].workload = runMeta.workload || s.rawWorkload;
                            const color = monoMode ? monoColors[seriesIdx % monoColors.length] : plotColors[seriesIdx % plotColors.length];
                            // colour is based on the run/series index; dash is based on the metric index
                            const lineType = dashTypes[metricIndex % dashTypes.length];
                            if (!(newHiddenSeries && newHiddenSeries.indexOf(runMeta.name || runMeta.id) > -1)) {
                                // include metric/name metadata on the series option (preferred lookup in formatter)
                                echSeries.push({
                                    name: seriesName,
                                     type: 'line',
                                     yAxisIndex: metricIndex,
                                     data: points,
                                     showSymbol: false,
                                     lineStyle: { width: this.state.chartLineWidth, type: lineType },
                                     itemStyle: { color },
                                     sampling: 'lttb',
                                     meta: { runs: [runMeta], metric, workload: runMeta.workload || s.rawWorkload }
                                     // metadata is attached to each point in "data"; no series-level custom relied upon
                                 });
                             }
                             seriesIdx++;
                         });
                     } else {
                         s.data.sort((a, b) => a[0] - b[0]);
                         const earliest = s.data[0][0];
                         // keep points compact and store runs/metric in lookup
                         const points = s.data.map(pt => [pt[0] - earliest, pt[1]]);
                         const seriesName = s.id;
                        // accumulate runs per series id (store runs, metric and workload)
                        if (!seriesMetaLookup[seriesName]) seriesMetaLookup[seriesName] = { runs: [], metric, workload: s.rawWorkload };
                        seriesMetaLookup[seriesName].runs.push(...(s.custom.runs || []));
                        seriesMetaLookup[seriesName].metric = metric;
                        seriesMetaLookup[seriesName].workload = s.rawWorkload;
                         const color = monoMode ? monoColors[seriesIdx % monoColors.length] : plotColors[seriesIdx % monoColors.length];
                         const lineType = dashTypes[metricIndex % dashTypes.length];
                         if (!(newHiddenSeries && newHiddenSeries.indexOf(s.id) > -1)) {
                             // attach metric into custom payload for aggregated series too
                             echSeries.push({
                                name: seriesName,
                                 type: 'line',
                                 yAxisIndex: metricIndex,
                                 data: points,
                                 showSymbol: false,
                                 lineStyle: { width: this.state.chartLineWidth, type: lineType },
                                 itemStyle: { color },
                                 sampling: 'lttb',
                                 meta: { runs: s.custom.runs, metric, workload: s.rawWorkload }
                                 // metadata lives on each point (runs, metric)
                             });
                         }
                         seriesIdx++;
                     }
                 }
                 metricIndex++;
             }

             // smoothing
             if (newSmoothing > 0) {
                 echSeries.forEach(series => {
                     if (series.data && series.data.length > 0) {
                         series.data = calcEMA(series.data, newSmoothing);
                     }
                 });
             }

             // build legendSelected map to hide hiddenSeries if present
             const legendSelected = {};
             echSeries.forEach(s => {
                 legendSelected[s.name] = !(newHiddenSeries && newHiddenSeries.indexOf(s.name) > -1);
             });

             const chartTitle = (() => {
                 const experimentCount = new Set((newChartData.data || []).map(s => s.experimentName)).size;
                 return experimentCount === 1 ? (newChartData.data[0] && newChartData.data[0].experimentName) : `Multiple Experiments (${experimentCount})`;
             })();

             // Prefer explicit title from chartData (set by ChartPicker) when available
             const finalTitle = (newChartData && newChartData.title) ? newChartData.title : chartTitle;
 
             // Adjust grid left/right to account for any yAxis offsets so the plot fills the wrapper
             const baseGrid = { left: 60, right: 60, top: 60, bottom: 96 };
             const leftOffsets = echYAxes.filter(a => (a.position || 'left') === 'left').map(a => a.offset || 0);
             const rightOffsets = echYAxes.filter(a => (a.position || 'left') === 'right').map(a => a.offset || 0);
             const extraLeft = leftOffsets.length ? Math.max(...leftOffsets) : 0;
             const extraRight = rightOffsets.length ? Math.max(...rightOffsets) : 0;
             const adjustedGrid = { ...baseGrid, left: baseGrid.left + extraLeft, right: baseGrid.right + extraRight };
 
             const echOptions = {
                 title: {
                     text: finalTitle,
                     left: 'center',
                     top: 12,
                     textStyle: { fontSize: 16, fontWeight: '600', color: '#115785' }
                 },
                 // use per-chart toolbox that includes the remove button wired to this chart
                 toolbox: toolboxWithRemove,
                 // detailed tooltip formatter (always on) - show metric name above value when present
                 tooltip: {
                     trigger: 'axis',
                     axisPointer: { type: 'cross' },
                     // formatter closes over seriesMetaLookup to avoid per-point metadata
                     formatter: function(params) {
                        const ps = Array.isArray(params) ? params : [params];
                        // group series entries by workload
                        const grouped = {};
                        ps.forEach(p => {
                            const seriesName = p.seriesName || '';
                            const seriesUserOpt = p.series && p.series.userOptions ? p.series.userOptions : null;
                            // prefer the exact series option by index (avoids name collisions), then series.userOptions.meta, then lookup
                            const seriesOption = (typeof p.seriesIndex === 'number' && echSeries[p.seriesIndex]) ? echSeries[p.seriesIndex] : null;
                            const meta = (seriesOption && seriesOption.meta) ? seriesOption.meta
                                        : (seriesUserOpt && seriesUserOpt.meta) ? seriesUserOpt.meta
                                        : (seriesMetaLookup[seriesName] || { runs: [], metric: '', workload: seriesName });
                            const workload = meta.workload || seriesName;
                            const entry = {
                                seriesName,
                                metric: meta.metric || '',
                                value: p.value ? p.value[1] : '',
                                dt: p.value ? p.value[0] : '',
                                color: p.color,
                                runs: meta.runs || []
                            };
                            if (!grouped[workload]) grouped[workload] = [];
                            grouped[workload].push(entry);
                        });

                        // render HTML grouped by workload, showing metric before value for each series
                        let html = '';
                        Object.keys(grouped).forEach(workload => {
                            html += `<div style="font-weight:bold; margin-bottom:6px;">${workload}</div>`;
                            grouped[workload].forEach(e => {
                                html += `<div style="color:${e.color}"><b>${e.metric}</b> — <b>${e.seriesName}</b>: ${e.value}</div>`;
                                html += `<div style="font-size:smaller">Time: ${milliToMinsSecs(e.dt)}</div>`;
                                if (e.runs && e.runs.length) {
                                    const models = [...new Set(e.runs.map(r=>r.model).filter(Boolean))];
                                    const sources = [...new Set(e.runs.map(r=>r.source).filter(Boolean))];
                                    const paramsSet = [...new Set(e.runs.map(r=>r.params).filter(Boolean))];
                                    const letters = [...new Set(e.runs.map(r=>r.letter).filter(Boolean))];
                                    if (models.length) html += `<div style="font-size:smaller"><b>Model(s):</b> ${models.join(', ')}</div>`;
                                    if (sources.length) html += `<div style="font-size:smaller"><b>Source(s):</b> ${sources.join(', ')}</div>`;
                                    if (paramsSet.length) html += `<div style="font-size:smaller"><b>Param(s):</b> ${paramsSet.join(', ')}</div>`;
                                    if (letters.length) html += `<div style="font-size:smaller"><b>Run(s):</b> ${letters.join(', ')}</div>`;
                                }
                                html += '<hr/>';
                            });
                        });
                        return html;
                     }
                 },
                  // keep legend at bottom and leave room in grid.bottom
                  legend: { data: echSeries.map(s => s.name), selected: legendSelected, bottom: 48 },
                  xAxis: { type: 'time', axisLabel: { formatter: (val) => milliToMinsSecs(val) } },
                  yAxis: echYAxes,
                  series: echSeries,
                  // ensure slider sits below the legend
                  dataZoom: [{ type: 'inside', xAxisIndex: [0] }, { type: 'slider', xAxisIndex: [0], bottom: 12 }],
                  grid: adjustedGrid
              };
             // ensure echOptions uses the prepared toolbox (avoid accidental overwrite below)
             echOptions.toolbox = toolboxWithRemove;

            if (this.ecInstance) {
                try { this.ecInstance.setOption(echOptions, true); } catch (e) {}
                setTimeout(() => { try { this.ecInstance.resize(); } catch (e) {} }, 0);
                // update state fields except options to avoid triggering ReactECharts full update
                this.setState({
                    id: newChartData.id,
                    data: newChartData.data,
                    shownRuns: newShownRuns,
                    hiddenSeries: newHiddenSeries,
                    smoothing: newSmoothing,
                    monochromeMode: monoMode,
                    loading: false
                });
            } else {
                // echarts not ready yet — set options in state so ReactECharts mounts with them
                this.setState({
                    id: newChartData.id,
                    data: newChartData.data,
                    options: echOptions,
                    shownRuns: newShownRuns,
                    hiddenSeries: newHiddenSeries,
                    smoothing: newSmoothing,
                    monochromeMode: monoMode,
                    loading: false
                });
            }
            return;
        }
 
        // SINGLE-AXIS mode
        const data = newChartData.data || [];
        const experimentList = new Set();
        const seriesMap = new Map(); // workloadId -> { data: [], custom: { runs: [] } }

        data.forEach(run => {
            if (!run.data) return;
            experimentList.add(run.experimentName);
            let workloadId = run.workload || '';
            if (workloadId.substring(workloadId.indexOf("-") + 1) === "null" || (newShownRuns && newShownRuns.indexOf(workloadId) > -1)) {
                if (run.letter === null || run.letter === undefined) {
                    const removeNull = workloadId.substring(0, workloadId.indexOf("-"));
                    workloadId = run.run_name ? run.run_name : (removeNull + " (" + run.name.substring(0, 5) + ")");
                } else {
                    if (run.letter.length > 1) {
                        workloadId = workloadId + " " + run.letter;
                    } else {
                        workloadId = run.run_name ? run.run_name : (" (" + run.name.substring(0, 5) + ")");
                    }
                }
            }

            if (!seriesMap.has(workloadId)) {
                seriesMap.set(workloadId, { id: workloadId, data: [], custom: { runs: [] } });
            }
            const s = seriesMap.get(workloadId);
            const metadata = { ...run };
            delete metadata.data;
            s.custom.runs.push(metadata);
            (run.data || []).forEach(d => {
                s.data.push([d.timestamp, d.value]);
            });
        });

        // Build echarts series array
        const echSeriesSingle = [];
        const seriesMetaLookupSingle = {};
        let monoCounter = 0;
        for (const [id, s] of seriesMap.entries()) {
             if (!s.data || s.data.length === 0) continue;
             s.data.sort((a, b) => a[0] - b[0]);
             const earliest = s.data[0][0];
             const points = s.data.map(pt => [pt[0] - earliest, pt[1]]);
            // accumulate runs per series id (store runs, metric, workload)
            if (!seriesMetaLookupSingle[s.id]) seriesMetaLookupSingle[s.id] = { runs: [], metric: newChartData.metric || '', workload: (s.custom.runs && s.custom.runs[0] && s.custom.runs[0].workload) || s.id };
            seriesMetaLookupSingle[s.id].runs.push(...(s.custom.runs || []));
             const name = s.id;
             if (newHiddenSeries && newHiddenSeries.indexOf(name) > -1) continue; // skip hidden
             // use seriesIdx for color (per-run/workload), and metricIndex to select dash style
             const color = monoMode ? monoColors[monoCounter % monoColors.length] : plotColors[monoCounter % monoColors.length];
             const lineType = dashTypes[0]; // single-axis always uses solid lines
             echSeriesSingle.push({
                 name,
                 type: 'line',
                 showSymbol: false,
                 data: points,
                 lineStyle: { width: this.state.chartLineWidth, type: lineType },
                 itemStyle: { color },
                 sampling: 'lttb',
                 // metadata attached to each point; no reliance on series.custom
             });
             monoCounter++;
         }
 
        // apply smoothing
        if (newSmoothing > 0) {
            echSeriesSingle.forEach(series => {
                if (series.data && series.data.length > 0) {
                    series.data = calcEMA(series.data, newSmoothing);
                }
            });
        }

        const chartTitle = (experimentList.size === 1) ? [...experimentList][0] : `Multiple Experiments (${experimentList.size})`;
        const finalSingleTitle = (newChartData && newChartData.title) ? newChartData.title : chartTitle;
 
         // legend selected map
         const legendSelectedSingle = {};
         echSeriesSingle.forEach(s => { legendSelectedSingle[s.name] = !(newHiddenSeries && newHiddenSeries.indexOf(s.name) > -1); });

         const echOptionsSingle = {
             title: {
                 text: finalSingleTitle,
                 left: 'center',
                 top: 12,
                 textStyle: { fontSize: 16, fontWeight: '600', color: '#115785' }
             },
            // use per-chart toolbox that includes the remove button wired to this chart
            toolbox: toolboxWithRemove,
              // detailed tooltip formatter (always on)
              tooltip: {
                  trigger: 'axis',
                  // use the per-render lookup for single-axis series
                  formatter: function(params) {
                     const ps = Array.isArray(params) ? params : [params];
                     // group entries by workload similar to multi-axis
                     const grouped = {};
                     ps.forEach(p => {
                         const seriesName = p.seriesName || '';
                         const seriesUserOpt = p.series && p.series.userOptions ? p.series.userOptions : null;
                         // prefer the exact series option by index (echSeriesSingle is out of scope here),
                         // so check p.series.userOptions.meta first then fall back to lookup
                         const meta = (seriesUserOpt && seriesUserOpt.meta) ? seriesUserOpt.meta : (seriesMetaLookupSingle[seriesName] || { runs: [], metric: '', workload: seriesName });
                         const workload = meta.workload || seriesName;
                         const entry = {
                             seriesName,
                             metric: meta.metric || '',
                             value: p.value ? p.value[1] : '',
                             dt: p.value ? p.value[0] : '',
                             color: p.color,
                             runs: meta.runs || []
                         };
                         if (!grouped[workload]) grouped[workload] = [];
                         grouped[workload].push(entry);
                     });
                     let html = '';
                     Object.keys(grouped).forEach(w => {
                         html += `<div style="font-weight:bold; margin-bottom:6px;">${w}</div>`;
                         grouped[w].forEach(e => {
                             html += `<div style="color:${e.color}"><b>${e.metric}</b> — <b>${e.seriesName}</b>: ${e.value}</div>`;
                             html += `<div style="font-size:smaller">Time: ${milliToMinsSecs(e.dt)}</div>`;
                             if (e.runs && e.runs.length) {
                                 const models = [...new Set(e.runs.map(r=>r.model).filter(Boolean))];
                                 const sources = [...new Set(e.runs.map(r=>r.source).filter(Boolean))];
                                 const paramsSet = [...new Set(e.runs.map(r=>r.params).filter(Boolean))];
                                 const letters = [...new Set(e.runs.map(r=>r.letter).filter(Boolean))];
                                 if (models.length) html += `<div style="font-size:smaller"><b>Model(s):</b> ${models.join(', ')}</div>`;
                                 if (sources.length) html += `<div style="font-size:smaller"><b>Source(s):</b> ${sources.join(', ')}</div>`;
                                 if (paramsSet.length) html += `<div style="font-size:smaller"><b>Param(s):</b> ${paramsSet.join(', ')}</div>`;
                                 if (letters.length) html += `<div style="font-size:smaller"><b>Run(s):</b> ${letters.join(', ')}</div>`;
                             }
                             html += '<hr/>';
                         });
                     });
                     return html;
                 }
              },
              // single-axis legend also at bottom
              legend: { data: echSeriesSingle.map(s => s.name), selected: legendSelectedSingle, bottom: 48 },
              xAxis: { type: 'time', axisLabel: { formatter: (val) => milliToMinsSecs(val) } },
              yAxis: [{ type: 'value', name: newChartData.metric || 'Value', nameLocation: 'middle', nameGap: 50}],
              series: echSeriesSingle,
              // ensure slider sits below the legend and reserve bottom space
              dataZoom: [{ type: 'inside', xAxisIndex: [0] }, { type: 'slider', xAxisIndex: [0], bottom: 12 }],
              grid: { left: 60, right: 60, top: 60, bottom: 96 }
         };
        // ensure echOptionsSingle has the toolbox set
        echOptionsSingle.toolbox = toolboxWithRemove;

        if (this.ecInstance) {
            try { this.ecInstance.setOption(echOptionsSingle, true); } catch (e) {}
            setTimeout(() => { try { this.ecInstance.resize(); } catch (e) {} }, 0);
            this.setState({
                id: newChartData.id,
                data: newChartData.data,
                shownRuns: newShownRuns,
                hiddenSeries: newHiddenSeries,
                smoothing: newSmoothing,
                monochromeMode: monoMode,
                loading: false
            });
        } else {
            this.setState({
                id: newChartData.id,
                data: newChartData.data,
                options: echOptionsSingle,
                shownRuns: newShownRuns,
                hiddenSeries: newHiddenSeries,
                smoothing: newSmoothing,
                monochromeMode: monoMode,
                loading: false
            });
        }
    }

    // updates smoothness state 
    handleSetSmoothness(smoothing) { 
        if (smoothing !== this.state.smoothing) {
            this.generateSeries(this.props.chartData, smoothing, this.state.shownRuns, this.state.hiddenSeries, this.state.range, this.state.monochromeMode);
        }
    }

    // controls which workloads show their runs
    toggleShownRuns(workloadId, event) {
		const toAdd = event.target.checked;
		this.setState(prev => {
			const shownRuns = [...prev.shownRuns];
			const idx = shownRuns.indexOf(workloadId);
			if (toAdd) {
				if (idx === -1) shownRuns.push(workloadId);
			} else {
				if (idx > -1) shownRuns.splice(idx, 1);
			}
			return { shownRuns };
		}, () => {
			// regenerate series using the UPDATED state (ensures multi-axis branch sees it)
			this.generateSeries(this.props.chartData, this.state.smoothing, this.state.shownRuns, this.state.hiddenSeries, this.state.range, this.state.monochromeMode);
			// notify parent about the updated chart extras (so ChartPicker can persist if needed)
			if (this.props.pullChartExtras) {
				this.props.pullChartExtras(this.state.id, this.state.smoothing, this.state.shownRuns, this.state.hiddenSeries, this.state.range);
			}
		});
	}

    // controls the series visibility after the legend item is clicked
    toggleSeriesVisibility(event) {
        // prevent default highcharts behaviour
        event.preventDefault();

        // add/remove series name from an array
        const newHiddenSeries = [...this.state.hiddenSeries];
        if (event.target.visible === true) {
            // will be set to invisible
            const seriesName = event.target.legendItem.textStr;

            if (newHiddenSeries.indexOf(seriesName) === -1) {
                newHiddenSeries.push(seriesName);
            }  
        }
        else {
            // will be set to visible
            const seriesName = event.target.legendItem.textStr;
            if (newHiddenSeries.indexOf(seriesName) > -1) {
                newHiddenSeries.splice(newHiddenSeries.indexOf(seriesName), 1);
            } 
        }

        // update chart and state
        this.generateSeries(this.props.chartData, this.state.smoothing, this.state.shownRuns, newHiddenSeries, this.state.range, this.state.monochromeMode); 
    }

    // controls the boost setting
    handleBoostSwitch(event) {
        // ECharts does not have the same boost mode; keep a local toggle only
        this.setState({
            boostMode: event.currentTarget.checked
        });
    }

    // controls the monochrome setting
    handleMonochromeModeSwitch(event) {
        const setMonochromeMode = !!event.currentTarget.checked;
        const seriesCount = (this.state.options && this.state.options.series) ? this.state.options.series.length : 0;
        if (seriesCount <= 5) {
            this.generateSeries(this.props.chartData, this.state.smoothing, this.state.shownRuns, this.state.hiddenSeries, this.state.range, setMonochromeMode);
        } else {
            this.setState({ monochromeMode: false });
            alert("Monochrome mode incompatible with more than 5 series.");
        }
    }

    // controls the line width setting (updates series lineStyle.width)
    handleSetLineWidth(newLineWidth) { 
        newLineWidth = parseFloat(newLineWidth);
        // update current options.series lineStyle width
        const opts = { ...this.state.options };
        if (opts.series && Array.isArray(opts.series)) {
            opts.series = opts.series.map(s => {
                return {
                    ...s,
                    lineStyle: { ...(s.lineStyle || {}), width: newLineWidth }
                };
            });
        }
        this.setState({
            chartLineWidth: newLineWidth,
            options: opts
        });
    }

    // records the range (zoom) setting so it is saved when you download/upload charts
    handleAfterSetExtremes = (event) => {
        const newRange = {min: event.min, max: event.max}
        this.setState({range: newRange});
    }

    // NEW: toggle SVG renderer and bump rendererVersion to force ReactECharts remount
	handleSvgRendererSwitch(event) {
		const enabled = !!event.currentTarget.checked;
		this.setState(prev => ({
			svgRendererEnabled: enabled,
			rendererVersion: (prev.rendererVersion || 0) + 1
		}));
	}

    render() {
        const { options, id, workloads, smoothing, shownRuns, rendererVersion, svgRendererEnabled } = this.state;
        const fullHeight = !!this.props.fullHeight;
        // When fullHeight, set wrapper height; do NOT set paddingBottom here (CSS gives 10px)
        const wrapperStyle = fullHeight
            ? { height: 'calc(100vh - 40px)', boxSizing: 'border-box' }
            : { height: 'calc(45vh - 20px)', boxSizing: 'border-box' };
        return (
            <div ref={this.wrapperRef} className="chartWrapper" style={wrapperStyle}>
                <div className="chartArea">
                     {this.state.wrapperReady ? (
                         <ReactECharts
                             key={`ech-${rendererVersion}-${id || 'noid'}`}          // force remount when rendererVersion changes
                             ref={this.chartRef}
                             option={options}
                             style={{ height: '100%', width: '100%' }}
                             opts={{ renderer: svgRendererEnabled ? 'svg' : 'canvas'}} // set renderer
                             onChartReady={this.afterChartCreated}
                         />
                     ) : null}
                 </div>
                <div id="workloadGroupingControlsWrapper" className={workloads.length === 0 ? "hide" : null}>
                    Toggle Runs:
                    {workloads.map(workload => (
                        <div key={workload}>
                            {workload}
                            <label className="switch" title={"Show individual runs for " + workload}>
                                <input 
                                    type="checkbox" 
                                    onChange={this.handleShowRunsSwitch(workload)} 
                                    checked={shownRuns.includes(workload)}
                                />
                                <span className="slider round"></span>
                            </label>
                        </div>        
                    ))}
                </div>
                <div id="exportOptionsWrapper">
                    <SmoothnessSlider 
                        onSetSmoothness={this.handleSetSmoothness.bind(this)}
                        defaultValue={smoothing}
                    />
                 {/* export controls follow */}
                     <div title="Convert chart to black and white (only supports 5 series)">
                         Monochrome Mode: <label className="switch">
                             <input 
                                 type="checkbox" 
                                 onChange={this.handleMonochromeModeSwitch.bind(this)} 
                                 checked={this.state.monochromeMode}
                             />
                             <span className="slider round"></span>
                         </label>
                     </div>   

                     {/* REPLACED: Boost toggle -> SVG Renderer toggle */}
                     <div title="Use SVG renderer (may be slower) - requires remount">
                         SVG Renderer: <label className="switch">
                             <input 
                                 type="checkbox" 
                                 onChange={this.handleSvgRendererSwitch.bind(this)} 
                                 checked={this.state.svgRendererEnabled}
                             />
                             <span className="slider round"></span>
                         </label>         
                     </div>

                     <div title="Adjust series line width (also updates ECharts line width)">
                         <LineWidthSlider 
                             onSetLineWidth={this.handleSetLineWidth.bind(this)}
                             defaultValue={this.state.chartLineWidth}
                         />        
                     </div>
                </div>
            </div>
        );
    }
}

/* Chart functional components */
function SmoothnessSlider(props) {
    const smoothSlider = useRef();
    const { onSetSmoothness } = props;
    const [smoothness, setSmoothness] = useState(0);   
    
    useEffect(() => {
        setSmoothness(props.defaultValue);
    }, [props.defaultValue]);

    useEffect(() => {
        if (smoothSlider.current) smoothSlider.current.addEventListener('change', e => onSetSmoothness(e.target.value));
    }, [onSetSmoothness]);
    const handleShowSmoothness = e => setSmoothness(e.target.value);
    return (
        <div id="smootherWrapper">
            <label htmlFor="smoother">Smoothness: </label>
            <input ref={smoothSlider} onChange={handleShowSmoothness} value={smoothness} type="range" name="smoother" min="0" max="99" /> 
            {smoothness}%
        </div>
    );
}
function LineWidthSlider(props) {
    const lineWidthSlider = useRef();
    const { onSetLineWidth } = props;
    const [lineWidth, setLineWidth] = useState(0);

    useEffect(() => setLineWidth(props.defaultValue), [props.defaultValue]);
    useEffect(() => {
        if (lineWidthSlider.current) lineWidthSlider.current.addEventListener('change', e => onSetLineWidth(e.target.value));
    }, [onSetLineWidth]);
    const handleShowLineWidth = e => setLineWidth(e.target.value);

    return (
        <div id="lineWidthWrapper">
            <label htmlFor="lineWidthSetter">Line Width: </label>
            <input ref={lineWidthSlider} onChange={handleShowLineWidth} value={lineWidth} type="range" name="lineWidthSetter" min="0.5" max="8.0" step="0.1" /> 
            <span>{lineWidth.toString().length === 1 ? lineWidth + ".0" : lineWidth}</span>
        </div>
    );

}

/* Chart helper functions */
function milliToMinsSecs(ms) {
    let label;
    let numOfDays = Math.trunc(ms / 86400000);
    if (numOfDays > 0) {
        label = numOfDays + "d " + new Date(ms).toISOString().slice(11, 19);
    }
    else {
        label = new Date(ms).toISOString().slice(11, 19);
    }
    return label;
}
function calcEMA(series, smoothingWeight) {

    // calculate smoothness using the smoothingWeight divided by the max smoothness
    const smoothness = smoothingWeight / 100;

    if (!series || series.length === 0) return series;
    // separate data from timestamps; support series entries that are either arrays [t,v] or objects { value: [t,v], ...meta}
    let time = series.map(a => Array.isArray(a) ? a[0] : (a && a.value ? a.value[0] : undefined));
    let data = series.map(a => Array.isArray(a) ? a[1] : (a && a.value ? a.value[1] : undefined));

    // first item is just first data item
    let emaData = [data[0]]; 

    // apply smoothing according to range and add to new EMA array    
    for (var i = 1; i < series.length; i++) {
        const emaResult = data[i] * (1 - smoothness) + emaData[i - 1] * (smoothness);
        emaData.push(emaResult.toFixed(4) * 1);
    }

    // recombine the new EMA array with the timestamp array
    let emaSeries = [];
    for (let i = 0; i < emaData.length; i++) {
        // preserve input shape: if original was array, output array; if object, output object preserving meta fields
        const original = series[i];
        if (Array.isArray(original)) {
            emaSeries.push([time[i], emaData[i]]);
        } else if (original && original.value) {
            // copy meta fields except overwrite value
            const copy = { ...original, value: [time[i], emaData[i]] };
            emaSeries.push(copy);
        } else {
            emaSeries.push([time[i], emaData[i]]);
        }
    }

    // return final series for highcharts API
    return emaSeries;
}

export default Chart;
