// WhatsApp alerts via CallMeBot — same service/API pattern as the Python
// bots' WhatsAppNotifier, reusing the exact same env vars so an existing
// CallMeBot registration works without any new setup.
const CALLMEBOT_URL = "https://api.callmebot.com/whatsapp.php";

function isEnabled(): boolean {
  return (process.env.WHATSAPP_ENABLED || "").toLowerCase() === "true";
}

export async function sendWhatsApp(subject: string, body: string): Promise<void> {
  if (!isEnabled()) return;

  const phone = process.env.WHATSAPP_PHONE;
  const apikey = process.env.WHATSAPP_APIKEY;
  if (!phone || !apikey) {
    throw new Error("WHATSAPP_ENABLED is true but WHATSAPP_PHONE or WHATSAPP_APIKEY is missing.");
  }

  const text = encodeURIComponent(`*${subject}*\n${body}`);
  const url = `${CALLMEBOT_URL}?phone=${phone}&text=${text}&apikey=${apikey}`;

  const res = await fetch(url);
  if (!res.ok) {
    const responseBody = await res.text().catch(() => "<no body>");
    throw new Error(`CallMeBot returned HTTP ${res.status}: ${responseBody}`);
  }
}
