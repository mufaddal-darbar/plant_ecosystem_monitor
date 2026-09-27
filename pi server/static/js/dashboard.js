let battChart, soilVpdChart;

// ================= AMBIENT 3D BACKGROUND PARTICLES =================
function init3DAmbientCanvas() {
  const canvas = document.createElement('canvas');
  canvas.id = 'ambient-canvas';
  document.body.prepend(canvas);

  const ctx = canvas.getContext('2d');
  let width, height;
  let particles = [];
  const count = 38;

  function resize() {
    width = canvas.width = window.innerWidth;
    height = canvas.height = window.innerHeight;
  }
  window.addEventListener('resize', resize);
  resize();

  for (let i = 0; i < count; i++) {
    particles.push({
      x: Math.random() * width,
      y: Math.random() * height,
      z: Math.random() * 0.8 + 0.2, // 3D depth layer
      vx: (Math.random() - 0.5) * 0.4,
      vy: (Math.random() - 0.5) * 0.4,
      radius: Math.random() * 2 + 1,
      color: Math.random() > 0.5 ? 'rgba(16, 185, 129,' : 'rgba(99, 102, 241,'
    });
  }

  let mouseX = width / 2;
  let mouseY = height / 2;
  window.addEventListener('mousemove', (e) => {
    mouseX = e.clientX;
    mouseY = e.clientY;
  });

  function render() {
    ctx.clearRect(0, 0, width, height);

    for (let i = 0; i < count; i++) {
      let p = particles[i];
      p.x += p.vx * p.z;
      p.y += p.vy * p.z;

      // Mouse interactive deflection in 3D
      const dx = mouseX - p.x;
      const dy = mouseY - p.y;
      const dist = Math.sqrt(dx * dx + dy * dy);
      if (dist < 180) {
        p.x -= (dx / dist) * 0.6 * p.z;
        p.y -= (dy / dist) * 0.6 * p.z;
      }

      if (p.x < 0) p.x = width;
      if (p.x > width) p.x = 0;
      if (p.y < 0) p.y = height;
      if (p.y > height) p.y = 0;

      ctx.beginPath();
      ctx.arc(p.x, p.y, p.radius * p.z, 0, Math.PI * 2);
      ctx.fillStyle = `${p.color}${(0.25 * p.z).toFixed(2)})`;
      ctx.fill();

      // Connect near neighbors with faint light strands
      for (let j = i + 1; j < count; j++) {
        let p2 = particles[j];
        let ndx = p.x - p2.x;
        let ndy = p.y - p2.y;
        let ndist = Math.sqrt(ndx * ndx + ndy * ndy);
        if (ndist < 130) {
          ctx.beginPath();
          ctx.moveTo(p.x, p.y);
          ctx.lineTo(p2.x, p2.y);
          ctx.strokeStyle = `rgba(255, 255, 255, ${(0.03 * (1 - ndist / 130)).toFixed(3)})`;
          ctx.stroke();
        }
      }
    }
    requestAnimationFrame(render);
  }
  render();
}

// ================= FULL 3D INTERACTIVE TILT =================
function init3DTiltSystem() {
  const tiltElements = document.querySelectorAll('.metric-card, .chart-container, .strip-card, .mini-card');

  tiltElements.forEach(el => {
    // Add specular glare overlay
    let glare = el.querySelector('.card-glare');
    if (!glare && (el.classList.contains('metric-card') || el.classList.contains('chart-container'))) {
      glare = document.createElement('div');
      glare.className = 'card-glare';
      el.appendChild(glare);
    }

    el.addEventListener('mousemove', (e) => {
      const rect = el.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      const centerX = rect.width / 2;
      const centerY = rect.height / 2;

      const maxTilt = el.classList.contains('metric-card') ? 10 : 5;
      const rotateX = ((y - centerY) / centerY) * -maxTilt;
      const rotateY = ((x - centerX) / centerX) * maxTilt;

      el.style.transform = `perspective(1000px) rotateX(${rotateX.toFixed(2)}deg) rotateY(${rotateY.toFixed(2)}deg) scale3d(1.02, 1.02, 1.02)`;

      if (glare) {
        glare.style.opacity = '1';
        glare.style.background = `radial-gradient(circle at ${x}px ${y}px, rgba(255,255,255,0.2) 0%, transparent 60%)`;
      }
    });

    el.addEventListener('mouseleave', () => {
      el.style.transform = 'perspective(1000px) rotateX(0deg) rotateY(0deg) scale3d(1, 1, 1)';
      if (glare) glare.style.opacity = '0';
    });
  });
}

function switchNavTab(e, tabId) {
  document.querySelectorAll('.tab-panel').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));

  document.getElementById(tabId).classList.add('active');
  e.currentTarget.classList.add('active');
}

function setGaugeProgress(id, pct) {
  const circle = document.getElementById(id);
  if (!circle) return;
  const radius = circle.r.baseVal.value;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (Math.min(Math.max(pct, 0), 100) / 100 * circumference);
  circle.style.strokeDasharray = `${circumference}`;
  circle.style.strokeDashoffset = offset;
}

function initCharts() {
  const gridConfig = { color: 'rgba(255, 255, 255, 0.04)', drawBorder: false };
  const tickConfig = { color: '#64748b', font: { family: "'JetBrains Mono', monospace", size: 10 } };

  const ctxBatt = document.getElementById('chartBattery').getContext('2d');
  const gradBatt = ctxBatt.createLinearGradient(0, 0, 0, 240);
  gradBatt.addColorStop(0, 'rgba(99, 102, 241, 0.35)');
  gradBatt.addColorStop(1, 'rgba(99, 102, 241, 0.0)');

  battChart = new Chart(ctxBatt, {
    type: 'line',
    data: {
      labels: [],
      datasets: [{
        label: 'Battery (V)',
        data: [],
        borderColor: '#6366f1',
        backgroundColor: gradBatt,
        fill: true,
        tension: 0.35,
        borderWidth: 2.5,
        pointRadius: 2.5,
        pointBackgroundColor: '#6366f1'
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: gridConfig, ticks: { ...tickConfig, maxTicksLimit: 8 } },
        y: { min: 3.2, max: 4.25, grid: gridConfig, ticks: tickConfig }
      }
    }
  });

  const ctxSoil = document.getElementById('chartSoilVpd').getContext('2d');
  const gradSoil = ctxSoil.createLinearGradient(0, 0, 0, 240);
  gradSoil.addColorStop(0, 'rgba(16, 185, 129, 0.3)');
  gradSoil.addColorStop(1, 'rgba(16, 185, 129, 0.0)');

  soilVpdChart = new Chart(ctxSoil, {
    type: 'line',
    data: {
      labels: [],
      datasets: [
        {
          label: 'Soil (%)',
          data: [],
          borderColor: '#10b981',
          backgroundColor: gradSoil,
          fill: true,
          yAxisID: 'ySoil',
          tension: 0.35,
          borderWidth: 2.5,
          pointRadius: 2.5,
          pointBackgroundColor: '#10b981'
        },
        {
          label: 'VPD (kPa)',
          data: [],
          borderColor: '#f59e0b',
          borderDash: [4, 4],
          yAxisID: 'yVpd',
          tension: 0.35,
          borderWidth: 2,
          pointRadius: 0
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: gridConfig, ticks: { ...tickConfig, maxTicksLimit: 8 } },
        ySoil: { type: 'linear', position: 'left', min: 0, max: 100, grid: gridConfig, ticks: tickConfig },
        yVpd: { type: 'linear', position: 'right', min: 0, max: 2.8, grid: { display: false }, ticks: tickConfig }
      }
    }
  });
}

async function refreshDashboard() {
  try {
    const rLatest = await fetch('/api/latest');
    const d = await rLatest.json();
    if (!d || !d.id) return;

    document.getElementById('strip-rssi').innerText = `${d.wifi_rssi || -70} dBm`;
    const dewMargin = (d.temperature - d.dew_point).toFixed(1);
    document.getElementById('strip-dew').innerText = `+${dewMargin} °C margin`;
    document.getElementById('strip-solar').innerText = d.lux > 500 ? 'Direct Sunlight' : 'Low/Night Light';

    const soil = d.soil_moisture || 0;
    document.getElementById('val-soil').innerText = soil.toFixed(1);
    document.getElementById('val-soil-rate').innerText = (d.soil_depletion_rate || 0).toFixed(1);
    setGaugeProgress('meter-soil', soil);
    document.getElementById('text-meter-soil').innerText = `${Math.round(soil)}%`;

    const bSoil = document.getElementById('badge-soil');
    if (soil < 25) { bSoil.className = 'card-badge badge-crit'; bSoil.innerText = 'Drought'; }
    else if (soil < 35) { bSoil.className = 'card-badge badge-warn'; bSoil.innerText = 'Thirsty'; }
    else { bSoil.className = 'card-badge badge-good'; bSoil.innerText = 'Optimal'; }

    const battV = d.battery_voltage || 0;
    const battPct = d.battery_percent || 0;
    document.getElementById('val-batt-v').innerText = battV.toFixed(2);
    setGaugeProgress('meter-batt', battPct);
    document.getElementById('text-meter-batt').innerText = `${battPct}%`;
    document.getElementById('badge-batt').innerText = `${battPct}% Storage`;

    const bRate = d.batt_rate_mvh || 0;
    const bSign = bRate > 0 ? '+ ' : '';
    document.getElementById('val-batt-rate').innerText = `${bSign}${bRate.toFixed(1)}`;

    const vpd = d.vpd || 0;
    document.getElementById('val-vpd').innerText = vpd.toFixed(2);
    setGaugeProgress('meter-vpd', (vpd / 2.5) * 100);
    document.getElementById('text-meter-vpd').innerText = `${vpd.toFixed(1)}`;
    
    const bVpd = document.getElementById('badge-vpd');
    const vpdStatus = document.getElementById('val-transpiration');
    if (vpd >= 0.8 && vpd <= 1.2) {
      bVpd.className = 'card-badge badge-good'; bVpd.innerText = 'Sweet Spot';
      vpdStatus.innerText = 'Optimal Stomatal Flow';
    } else if (vpd > 1.2) {
      bVpd.className = 'card-badge badge-warn'; bVpd.innerText = 'High Demand';
      vpdStatus.innerText = 'Rapid Transpiration';
    } else {
      bVpd.className = 'card-badge badge-neutral'; bVpd.innerText = 'Sluggish';
      vpdStatus.innerText = 'Stagnant Moisture';
    }

    const lux = d.lux || 0;
    document.getElementById('val-lux').innerText = Math.round(lux).toLocaleString();
    document.getElementById('val-dli').innerText = (d.dli || 0).toFixed(2);
    document.getElementById('badge-dli').innerText = `${(d.dli || 0).toFixed(1)} mol`;
    setGaugeProgress('meter-lux', Math.min((lux / 50000) * 100, 100));

    document.getElementById('val-temp').innerText = (d.temperature || 0).toFixed(1);
    document.getElementById('val-hum').innerText = (d.humidity || 0).toFixed(1);
    document.getElementById('val-dew').innerText = (d.dew_point || 0).toFixed(1);
    document.getElementById('val-raw-soil').innerText = d.raw_soil || '--';
    
    document.getElementById('val-power-state').innerText = bRate >= 0 ? 'Solar Charging' : 'Discharging';
    document.getElementById('val-power-sub').innerText = bRate >= 0 ? '☀️ Net Photovoltaic Gain' : '🔋 Drawing from Cell';
    document.getElementById('label-sync').innerText = `Synced: ${d.timestamp ? d.timestamp.split(' ')[1] : ''}`;

    const rHist = await fetch('/api/history?limit=36');
    const hist = await rHist.json();
    const labels = hist.map(h => (h.timestamp ? h.timestamp.split(' ')[1] : ''));

    battChart.data.labels = labels;
    battChart.data.datasets[0].data = hist.map(h => h.battery_voltage);
    battChart.update();

    soilVpdChart.data.labels = labels;
    soilVpdChart.data.datasets[0].data = hist.map(h => h.soil_moisture);
    soilVpdChart.data.datasets[1].data = hist.map(h => h.vpd);
    soilVpdChart.update();

  } catch (err) {
    console.error("Dashboard refresh failed:", err);
  }
}

async function loadCurrentConfig() {
  try {
    const res = await fetch('/api/config');
    const d = await res.json();
    document.getElementById('cfg_low_batt').value = d.low_batt;
    document.getElementById('cfg_night_lux').value = d.night_lux;
    document.getElementById('cfg_day_sleep').value = d.day_sleep;
    document.getElementById('cfg_night_sleep').value = d.night_sleep;
    document.getElementById('cfg-version-badge').innerText = `Version ${d.config_version}`;
    document.getElementById('strip-cfg-ver').innerText = `v${d.config_version}`;
  } catch (e) {
    console.error("Config fetch error:", e);
  }
}

async function saveRemoteConfig() {
  const payload = {
    low_batt: parseFloat(document.getElementById('cfg_low_batt').value),
    night_lux: parseFloat(document.getElementById('cfg_night_lux').value),
    day_sleep: parseInt(document.getElementById('cfg_day_sleep').value),
    night_sleep: parseInt(document.getElementById('cfg_night_sleep').value)
  };

  try {
    const res = await fetch('/api/config/update', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    if (res.ok) {
      const msg = document.getElementById('save-feedback');
      msg.style.display = 'flex';
      loadCurrentConfig();
      setTimeout(() => { msg.style.display = 'none'; }, 4000);
    }
  } catch (e) {
    alert("Failed to commit configuration: " + e);
  }
}

window.onload = () => {
  init3DAmbientCanvas();
  initCharts();
  refreshDashboard();
  loadCurrentConfig();
  init3DTiltSystem();
  setInterval(refreshDashboard, 15000);
};