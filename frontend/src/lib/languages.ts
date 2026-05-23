/**
 * Indian languages supported by the voice pipeline.
 * ISO codes match `agent/language.py::LANG_CONFIG`.
 */
export const LANGUAGES = [
  { code: "en", native: "English", english: "English", bcp47: "en-IN" },
  { code: "hi", native: "हिन्दी", english: "Hindi", bcp47: "hi-IN" },
  { code: "bn", native: "বাংলা", english: "Bengali", bcp47: "bn-IN" },
  { code: "ta", native: "தமிழ்", english: "Tamil", bcp47: "ta-IN" },
  { code: "te", native: "తెలుగు", english: "Telugu", bcp47: "te-IN" },
  { code: "kn", native: "ಕನ್ನಡ", english: "Kannada", bcp47: "kn-IN" },
  { code: "ml", native: "മലയാളം", english: "Malayalam", bcp47: "ml-IN" },
  { code: "mr", native: "मराठी", english: "Marathi", bcp47: "mr-IN" },
  { code: "gu", native: "ગુજરાતી", english: "Gujarati", bcp47: "gu-IN" },
  { code: "pa", native: "ਪੰਜਾਬੀ", english: "Punjabi", bcp47: "pa-IN" },
] as const;

export type LangCode = (typeof LANGUAGES)[number]["code"];

export const STATE_LABELS: Record<
  LangCode,
  { listening: string; thinking: string; speaking: string }
> = {
  en: { listening: "Listening…", thinking: "Thinking…", speaking: "Speaking…" },
  hi: { listening: "सुन रही हूँ…", thinking: "सोच रही हूँ…", speaking: "बोल रही हूँ…" },
  bn: { listening: "শুনছি…", thinking: "ভাবছি…", speaking: "বলছি…" },
  ta: { listening: "கேட்கிறேன்…", thinking: "யோசிக்கிறேன்…", speaking: "பேசுகிறேன்…" },
  te: { listening: "వింటున్నాను…", thinking: "ఆలోచిస్తున్నాను…", speaking: "మాట్లాడుతున్నాను…" },
  kn: { listening: "ಆಲಿಸುತ್ತಿದ್ದೇನೆ…", thinking: "ಯೋಚಿಸುತ್ತಿದ್ದೇನೆ…", speaking: "ಮಾತನಾಡುತ್ತಿದ್ದೇನೆ…" },
  ml: { listening: "കേൾക്കുന്നു…", thinking: "ചിന്തിക്കുന്നു…", speaking: "സംസാരിക്കുന്നു…" },
  mr: { listening: "ऐकत आहे…", thinking: "विचार करते…", speaking: "बोलते आहे…" },
  gu: { listening: "સાંભળું છું…", thinking: "વિચારું છું…", speaking: "બોલું છું…" },
  pa: { listening: "ਸੁਣ ਰਹੀ ਹਾਂ…", thinking: "ਸੋਚ ਰਹੀ ਹਾਂ…", speaking: "ਬੋਲ ਰਹੀ ਹਾਂ…" },
};

/** First-turn greeting — mirrors `agent/language.py` greeting_text. */
/** Mirrors `agent/language.py` greeting_text — keep in sync. */
export const AGENT_GREETINGS: Record<LangCode, string> = {
  en: "Hello, good morning. Hope you're doing well. How may I help you today?",
  hi: "नमस्ते जी, आप कैसे हैं? सब ठीक है ना? क्या डॉक्टर की अपॉइंटमेंट बुक करनी थी?",
  bn: "নমস্কার, আপনি কেমন আছেন? ডাক্তারের অ্যাপয়েন্টমেন্ট বুক করতে চেয়েছিলেন?",
  ta: "வணக்கம், நீங்கள் எப்படி இருக்கிறீர்கள்? மருத்துவர் சந்திப்பு பதிவு செய்ய வேண்டுமா?",
  te: "నమస్కారం, మీరు ఎలా ఉన్నారు? డాక్టర్ అపాయింట్‌మెంట్ బుక్ చేయాలనుకుంటున్నారా?",
  kn: "ನಮಸ್ಕಾರ, ನೀವು ಹೇಗಿದ್ದೀರಿ? ವೈದ್ಯರ ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್ ಬುಕ್ ಮಾಡಬೇಕೇ?",
  ml: "നമസ്കാരം, സുഖമാണോ? ഡോക്ടർ അപ്പോയിന്റ്മെന്റ് ബുക്ക് ചെയ്യണോ?",
  mr: "नमस्कार, तुम्ही कसे आहात? डॉक्टरची अपॉइंटमेंट बुक करायची आहे का?",
  gu: "નમસ્તે, તમે કેમ છો? ડૉક્ટરની એપોઇન્ટમેન્ટ બુક કરવી છે?",
  pa: "ਸਤ ਸ੍ਰੀ ਅਕਾਲ, ਤੁਸੀਂ ਠੀਕ ਹੋ? ਡਾਕਟਰ ਦੀ ਅਪਾਇੰਟਮੈਂਟ ਬੁਕ ਕਰਨੀ ਸੀ?",
};

/** When STT returns empty — ask to repeat. */
export const FILLER_DIDNT_CATCH: Record<LangCode, string> = {
  en: "Sorry, I didn't catch that — could you say it again?",
  hi: "माफ कीजिए, मैं समझ नहीं पाई। फिर से कहिए?",
  bn: "দুঃখিত, আমি শুনতে পাইনি। আবার বলবেন?",
  ta: "மன்னிக்கவும், எனக்கு கேட்கவில்லை. மீண்டும் சொல்லுங்கள்?",
  te: "క్షమించండి, నాకు వినిపించలేదు. మళ్ళీ చెప్పగలరా?",
  kn: "ಕ್ಷಮಿಸಿ, ನಾನು ಕೇಳಿಸಲಿಲ್ಲ. ಮತ್ತೊಮ್ಮೆ ಹೇಳುತ್ತೀರಾ?",
  ml: "ക്ഷമിക്കണം, എനിക്ക് കേൾക്കാനായില്ല. വീണ്ടും പറയാമോ?",
  mr: "माफ करा, मला ऐकू आले नाही. पुन्हा सांगाल का?",
  gu: "માફ કરશો, મને સંભળાયું નહીં. ફરી કહેશો?",
  pa: "ਮਾਫ ਕਰਨਾ, ਮੈਨੂੰ ਸੁਣਾਈ ਨਹੀਂ ਦਿੱਤਾ। ਕਿਰਪਾ ਕਰਕੇ ਦੁਬਾਰਾ ਦੱਸੋ?",
};
