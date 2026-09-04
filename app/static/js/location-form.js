/*
 * Position picker and address search for the station/incident forms.
 *
 * station-form.html and incident-form.html carried two near-identical copies of
 * this (~100 lines each) inside th:inline script blocks. One file, called from
 * both templates.
 */
function initLocationForm(options) {
    document.addEventListener('DOMContentLoaded', function () {
        var lat = options.lat;
        var lng = options.lng;

        var map = L.map('position-map').setView([lat, lng], 13);
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            maxZoom: 19,
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        }).addTo(map);

        var marker = L.marker([lat, lng], { draggable: true }).addTo(map);

        function setPosition(latlng) {
            marker.setLatLng(latlng);
            document.getElementById('createLat').value = latlng.lat.toFixed(6);
            document.getElementById('createLng').value = latlng.lng.toFixed(6);
        }

        marker.on('dragend', function (e) { setPosition(e.target.getLatLng()); });
        map.on('click', function (e) { setPosition(e.latlng); });

        // Leaflet measures the container on init; inside a form section that is
        // often before layout has settled.
        setTimeout(function () { map.invalidateSize(); }, 0);

        var errorDiv = document.getElementById('geocode-error');
        var searchBtn = document.getElementById('search-address-btn');
        var addressInput = document.getElementById('address-input');

        function showError(message) {
            errorDiv.textContent = message;
            errorDiv.style.display = 'block';
        }

        function hideError() {
            errorDiv.style.display = 'none';
        }

        function setLoading(isLoading) {
            searchBtn.textContent = isLoading ? 'Suche...' : 'Adresse suchen';
            searchBtn.disabled = isLoading;
        }

        function search() {
            var address = addressInput.value.trim();
            if (!address) {
                showError('Bitte geben Sie eine Adresse ein');
                return;
            }

            hideError();
            setLoading(true);

            fetch('/api/geocode?address=' + encodeURIComponent(address), { credentials: 'same-origin' })
                .then(function (response) {
                    if (response.status === 200) return response.json();
                    if (response.status === 404) throw new Error('Adresse nicht gefunden');
                    if (response.status === 429) throw new Error('Zu viele Anfragen, bitte kurz warten');
                    throw new Error('Fehler beim Laden der Adresse');
                })
                .then(function (data) {
                    setPosition(L.latLng(data.lat, data.lng));
                    map.panTo([data.lat, data.lng]);
                    addressInput.value = data.displayName;
                })
                .catch(function (err) {
                    showError(err.message || 'Fehler beim Laden der Adresse');
                })
                .finally(function () { setLoading(false); });
        }

        searchBtn.addEventListener('click', search);
        addressInput.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') {
                e.preventDefault();  // otherwise Enter submits the whole form
                search();
            }
        });
    });
}
