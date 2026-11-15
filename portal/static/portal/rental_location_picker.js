(() => {
  const root = document.getElementById("rental-location-picker");
  if (!root || !window.L) return;

  const label = document.getElementById("id_location_name");
  const latitude = document.getElementById("id_latitude");
  const longitude = document.getElementById("id_longitude");
  const searchInput = document.getElementById("rental-location-search");
  const searchButton = document.getElementById("rental-location-search-button");
  const resultsBox = document.getElementById("rental-location-results");
  const state = document.getElementById("rental-location-state");
  const mapElement = document.getElementById("rental-location-map");
  const map = L.map(mapElement, {scrollWheelZoom: false}).setView([-2.5, 118], 5);

  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>'
  }).addTo(map);

  let marker = null;
  let lastSearch = 0;
  const setPoint = (lat, lng, zoom = true) => {
    const point = L.latLng(Number(lat), Number(lng));
    if (!Number.isFinite(point.lat) || !Number.isFinite(point.lng)) return;
    if (!marker) {
      marker = L.marker(point, {draggable: true}).addTo(map);
      marker.on("dragend", () => updateFields(marker.getLatLng()));
    } else marker.setLatLng(point);
    updateFields(point);
    if (zoom) map.setView(point, 14);
  };
  const updateFields = (point) => {
    latitude.value = point.lat.toFixed(7);
    longitude.value = point.lng.toFixed(7);
    state.textContent = `Koordinat terpilih: ${latitude.value}, ${longitude.value}`;
    if (!label.value.trim()) label.value = `Titik peta ${latitude.value}, ${longitude.value}`;
  };
  if (latitude.value && longitude.value) setPoint(latitude.value, longitude.value);
  map.on("click", (event) => setPoint(event.latlng.lat, event.latlng.lng, false));

  const search = async () => {
    const query = searchInput.value.trim();
    if (query.length < 3) {
      state.textContent = "Ketik minimal 3 karakter untuk mencari lokasi.";
      return;
    }
    searchButton.disabled = true;
    resultsBox.replaceChildren();
    state.textContent = "Mencari lokasi...";
    try {
      const wait = Math.max(0, 1100 - (Date.now() - lastSearch));
      if (wait) await new Promise((resolve) => setTimeout(resolve, wait));
      const response = await fetch(`${root.dataset.searchUrl}?q=${encodeURIComponent(query)}`, {
        headers: {"Accept": "application/json"},
        credentials: "same-origin"
      });
      lastSearch = Date.now();
      const data = await response.json();
      if (!response.ok) throw new Error(data.message || "Pencarian lokasi gagal.");
      if (!data.results.length) {
        state.textContent = "Lokasi tidak ditemukan. Coba nama PT ditambah kota atau kabupaten.";
        return;
      }
      state.textContent = "Pilih hasil yang sesuai, lalu titiknya akan ditandai pada peta.";
      data.results.forEach((result) => {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = result.name;
        button.addEventListener("click", () => {
          label.value = result.name;
          setPoint(result.latitude, result.longitude);
          resultsBox.replaceChildren();
        });
        resultsBox.appendChild(button);
      });
    } catch (error) {
      state.textContent = error.message || "Pencarian gagal. Tandai titik langsung pada peta.";
    } finally {
      searchButton.disabled = false;
    }
  };

  searchButton.addEventListener("click", search);
  searchInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); search(); }
  });
})();
