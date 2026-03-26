function resizeCanvas(canvas, context) {
  const ratio = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  const width = Math.max(240, Math.floor(rect.width || canvas.clientWidth || 320));
  const height = Math.max(160, Math.floor(rect.height || canvas.clientHeight || 220));
  canvas.width = Math.floor(width * ratio);
  canvas.height = Math.floor(height * ratio);
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  return { width, height };
}

function drawEmptyState(context, width, height, message) {
  context.clearRect(0, 0, width, height);
  context.fillStyle = '#6e7681';
  context.font = '13px "SF Pro Display", "PingFang SC", sans-serif';
  context.textAlign = 'center';
  context.fillText(message, width / 2, height / 2);
}

function drawAxes(context, width, height) {
  context.strokeStyle = '#26303a';
  context.lineWidth = 1;
  context.beginPath();
  context.moveTo(36, 12);
  context.lineTo(36, height - 28);
  context.lineTo(width - 12, height - 28);
  context.stroke();
}

function drawLineChart(canvas, labels, values) {
  const context = canvas.getContext('2d');
  if (!context) {
    return;
  }
  const { width, height } = resizeCanvas(canvas, context);
  if (!values.length) {
    drawEmptyState(context, width, height, '等待运行数据...');
    return;
  }
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  drawAxes(context, width, height);
  const left = 36;
  const right = width - 12;
  const top = 12;
  const bottom = height - 28;
  context.strokeStyle = '#58a6ff';
  context.fillStyle = 'rgba(88,166,255,0.12)';
  context.lineWidth = 2;
  context.beginPath();
  values.forEach((value, index) => {
    const x = left + ((right - left) * index) / Math.max(values.length - 1, 1);
    const y = bottom - ((value - min) / span) * (bottom - top);
    if (index === 0) {
      context.moveTo(x, y);
    } else {
      context.lineTo(x, y);
    }
  });
  context.stroke();
  context.lineTo(right, bottom);
  context.lineTo(left, bottom);
  context.closePath();
  context.fill();
  context.fillStyle = '#9da7b3';
  context.font = '11px "SF Pro Display", "PingFang SC", sans-serif';
  context.textAlign = 'left';
  context.fillText(`$${Math.round(max).toLocaleString()}`, 6, top + 10);
  context.fillText(`$${Math.round(min).toLocaleString()}`, 6, bottom);
  context.textAlign = 'right';
  context.fillText(labels[labels.length - 1] || '', right, height - 8);
}

function drawBarChart(canvas, labels, values) {
  const context = canvas.getContext('2d');
  if (!context) {
    return;
  }
  const { width, height } = resizeCanvas(canvas, context);
  if (!values.length) {
    drawEmptyState(context, width, height, '等待实验结果...');
    return;
  }
  drawAxes(context, width, height);
  const left = 36;
  const right = width - 12;
  const top = 12;
  const bottom = height - 28;
  const max = Math.max(...values, 1);
  const barWidth = (right - left) / Math.max(values.length, 1);
  context.fillStyle = 'rgba(63,185,80,0.55)';
  context.strokeStyle = '#3fb950';
  values.forEach((value, index) => {
    const heightRatio = value / max;
    const x = left + barWidth * index + 4;
    const y = bottom - heightRatio * (bottom - top);
    const widthValue = Math.max(8, barWidth - 8);
    context.fillRect(x, y, widthValue, bottom - y);
    context.strokeRect(x, y, widthValue, bottom - y);
  });
  context.fillStyle = '#9da7b3';
  context.font = '11px "SF Pro Display", "PingFang SC", sans-serif';
  context.textAlign = 'left';
  context.fillText(max.toLocaleString(), 6, top + 10);
  context.textAlign = 'right';
  context.fillText(labels[labels.length - 1] || '', right, height - 8);
}

function noop() {}

export function createDashboardCharts() {
  const pnlCanvas = document.getElementById('pnlChart');
  const tokenCanvas = document.getElementById('tokenChart');
  if (!(pnlCanvas instanceof HTMLCanvasElement) || !(tokenCanvas instanceof HTMLCanvasElement)) {
    return {
      available: false,
      reset: noop,
      setTokenSeries: noop,
      addPnlPoint: noop,
      setPnlSeries: noop,
    };
  }

  const pnlLabels = [];
  const pnlValues = [];
  const tokenLabels = [];
  const tokenValues = [];

  function redraw() {
    drawLineChart(pnlCanvas, pnlLabels, pnlValues);
    drawBarChart(tokenCanvas, tokenLabels, tokenValues);
  }

  window.addEventListener('resize', redraw);
  redraw();

  return {
    available: true,
    reset() {
      pnlLabels.length = 0;
      pnlValues.length = 0;
      tokenLabels.length = 0;
      tokenValues.length = 0;
      redraw();
    },
    addPnlPoint(label, value) {
      pnlLabels.push(String(label ?? ''));
      pnlValues.push(Number(value || 0));
      drawLineChart(pnlCanvas, pnlLabels, pnlValues);
    },
    setPnlSeries(labels, values) {
      pnlLabels.splice(0, pnlLabels.length, ...labels.map((item) => String(item)));
      pnlValues.splice(0, pnlValues.length, ...values.map((item) => Number(item || 0)));
      drawLineChart(pnlCanvas, pnlLabels, pnlValues);
    },
    setTokenSeries(labels, values) {
      tokenLabels.splice(0, tokenLabels.length, ...labels.map((item) => String(item)));
      tokenValues.splice(0, tokenValues.length, ...values.map((item) => Number(item || 0)));
      drawBarChart(tokenCanvas, tokenLabels, tokenValues);
    },
  };
}
