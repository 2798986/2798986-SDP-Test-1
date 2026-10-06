/* RAT frontend - bootstrap placeholder. The full dashboard replaces this file
   in the UI milestone. */
(function () {
  "use strict";
  var content = document.getElementById("content");
  if (content) {
    content.innerHTML =
      '<div class="empty-state">' +
      '<h2>Server is running</h2>' +
      '<p>The dashboard shell is being built. The backend API is coming up next.</p>' +
      "</div>";
  }
})();
