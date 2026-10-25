const scanContainer = document.querySelector("[data-session-poller]");

if (scanContainer) {
    const statusMessage = document.querySelector("#scan-status");

    function checkSession() {
        fetch("/api/merchant/session-status", {cache: "no-store"})
            .then((response) => {
                if (response.status === 401 || response.status === 403) {
                    window.location.href = "/auth/login";
                    return null;
                }
                if (!response.ok) {
                    throw new Error(`Session check failed (${response.status}).`);
                }
                return response.json();
            })
            .then((data) => {
                if (!data) {
                    return;
                }
                if (data.status === "awaiting_amount") {
                    window.location.href =
                        `/merchant/enter_amount/${data.session_id}`;
                    return;
                }

                statusMessage.textContent = "";
                window.setTimeout(checkSession, 1000);
            })
            .catch((error) => {
                console.error("Error polling:", error);
                statusMessage.textContent =
                    "Unable to check for a card scan. Retrying shortly.";
                window.setTimeout(checkSession, 5000);
            });
    }

    window.setTimeout(checkSession, 1000);
}
