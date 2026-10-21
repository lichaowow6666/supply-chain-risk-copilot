export default function ({ parentElement, data, setTriggerValue }) {
  const host = parentElement.querySelector('.scenario-market-chart');
  const graph = document.createElement('div');
  graph.style.cssText = 'width:100%;height:100%';
  host.replaceChildren(graph);
  const plotly = window.Plotly;
  const controller = new AbortController();
  let disposed = false;
  let down = null;

  // Read Plotly's rendered axes so clicks still map to quote units after zoom
  // or resize. This deliberately does not depend on plotly_click, which only
  // fires near data points and cannot select arbitrary prices.
  function choice(event) {
    const layout = graph._fullLayout;
    if (!layout?.xaxis?.p2d || !layout?.yaxis?.p2d) return null;
    const bounds = graph.getBoundingClientRect();
    const px = event.clientX - bounds.left - layout.xaxis._offset;
    const py = event.clientY - bounds.top - layout.yaxis._offset;
    if (px < 0 || py < 0 || px > layout.xaxis._length || py > layout.yaxis._length) return null;
    const date = new Date(layout.xaxis.p2d(px)).getTime();
    const start = new Date(data.futureStart).getTime();
    const end = new Date(data.futureEnd).getTime();
    const price = Number(layout.yaxis.p2d(py));
    if (!Number.isFinite(date) || date < start || date > end
        || !Number.isFinite(price) || price < data.reference * .001) return null;
    return { price, x: px + layout.xaxis._offset, y: py + layout.yaxis._offset };
  }

  const guide = document.createElement('div');
  guide.style.cssText = 'display:none;position:absolute;pointer-events:none;border-top:1px dashed #e58c45;z-index:10';
  const label = document.createElement('div');
  label.style.cssText = 'display:none;position:absolute;pointer-events:none;padding:4px 7px;background:#fff7ed;color:#9a3412;font:12px sans-serif;border-radius:4px;z-index:11;white-space:nowrap';
  host.append(guide, label);
  function hide() { guide.style.display = label.style.display = 'none'; }
  function hover(event) {
    const picked = choice(event);
    if (!picked) { hide(); return; }
    const axis = graph._fullLayout.xaxis;
    guide.style.display = label.style.display = 'block';
    guide.style.left = `${axis._offset}px`;
    guide.style.top = `${picked.y}px`;
    guide.style.width = `${axis._length}px`;
    label.style.right = '16px';
    label.style.top = `${Math.max(0, picked.y - 29)}px`;
    label.textContent = `${picked.price.toFixed(4)} ${data.unit} · ${data.horizon}-session target`;
  }

  plotly.newPlot(graph, data.figure.data, data.figure.layout, {
    responsive: true,
    displaylogo: false,
    displayModeBar: false,
    scrollZoom: true,
  }).then(() => {
    if (disposed) return;
    const options = { capture: true, signal: controller.signal };
    graph.addEventListener('pointerdown', event => {
      if (event.button === 0) down = { x: event.clientX, y: event.clientY };
    }, options);
    // Plotly installs a document-level drag cover on pointerdown. The release
    // can land on that cover, so listening on the graph loses ordinary clicks.
    document.addEventListener('pointerup', event => {
      const start = down;
      down = null;
      if (!start || Math.hypot(event.clientX - start.x, event.clientY - start.y) > 5) return;
      const picked = choice(event);
      if (picked) {
        hide();
        // Horizontal location is only a pointer affordance: the selected price
        // remains the terminal target for the existing user-selected horizon.
        setTriggerValue('price', Number(picked.price.toPrecision(12)));
      }
    }, options);
    graph.addEventListener('pointermove', hover, options);
    graph.addEventListener('pointerleave', hide, options);
    graph.addEventListener('pointercancel', () => { down = null; hide(); }, options);
  });

  const resize = new ResizeObserver(() => {
    if (!disposed && graph._fullLayout) plotly.Plots.resize(graph);
  });
  resize.observe(host);
  return () => {
    disposed = true;
    controller.abort();
    resize.disconnect();
    plotly.purge(graph);
  };
}
