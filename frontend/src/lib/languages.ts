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
  en: "Hi! I can help you book an appointment. Please speak in English, Hindi, Tamil, or any language you are comfortable with. What do you need?",
  hi: "नमस्ते! मैं अपॉइंटमेंट बुक करने में मदद कर रही हूँ। आप हिन्दी, अंग्रेज़ी, तमिल या जिस भाषा में चाहें बात कर सकते हैं। आपको क्या चाहिए?",
  bn: "নমস্কার! আমি অ্যাপয়েন্টমেন্ট বুক করতে সাহায্য করছি। আপনি বাংলা, ইংরেজি, হিন্দি বা যেকোনো ভাষায় কথা বলতে পারেন। আপনার কী দরকার?",
  ta: "வணக்கம்! நான் சந்திப்பு பதிவு செய்ய உதவுகிறேன். தமிழ், ஆங்கிலம், ஹிந்தி அல்லது நீங்கள் விரும்பும் மொழியில் பேசலாம். உங்களுக்கு என்ன வேண்டும்?",
  te: "నమస్కారం! అపాయింట్‌మెంట్ బుక్ చేయడంలో నేను సహాయం చేస్తాను. తెలుగు, ఇంగ్లీష్, హిందీ లేదా మీకు సౌకర్యమైన భాషలో మాట్లాడవచ్చు. మీకు ఏమి కావాలి?",
  kn: "ನಮಸ್ಕಾರ! ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್ ಬುಕ್ ಮಾಡಲು ನಾನು ಸಹಾಯ ಮಾಡುತ್ತೇನೆ. ಕನ್ನಡ, ಇಂಗ್ಲಿಷ್, ಹಿಂದಿ ಅಥವಾ ನಿಮಗೆ ಸೌಕರ್ಯವಾದ ಭಾಷೆಯಲ್ಲಿ ಮಾತನಾಡಬಹುದು. ನಿಮಗೆ ಏನು ಬೇಕು?",
  ml: "നമസ്കാരം! അപ്പോയിന്റ്മെന്റ് ബുക്ക് ചെയ്യാൻ ഞാൻ സഹായിക്കും. മലയാളം, ഇംഗ്ലീഷ്, ഹിന്ദി അല്ലെങ്കിൽ നിങ്ങൾക്ക് സുഖമായ ഭാഷയിൽ സംസാരിക്കാം. നിങ്ങൾക്ക് എന്താണ് വേണ്ടത്?",
  mr: "नमस्कार! मी अपॉइंटमेंट बुक करण्यात मदत करते. मराठी, इंग्रजी, हिंदी किंवा तुम्हाला सोयीच्या भाषेत बोलू शकता. तुम्हाला काय हवे आहे?",
  gu: "નમસ્તે! હું એપોઇન્ટમેન્ટ બુક કરવામાં મદદ કરું છું. ગુજરાતી, અંગ્રેજી, હિન્દી અથવા તમને ગમતી ભાષામાં બોલી શકો છો. તમને શું જોઈએ છે?",
  pa: "ਸਤ ਸ੍ਰੀ ਅਕਾਲ! ਮੈਂ ਅਪਾਇੰਟਮੈਂਟ ਬੁਕ ਕਰਨ ਵਿੱਚ ਮਦਦ ਕਰਦੀ ਹਾਂ। ਪੰਜਾਬੀ, ਅੰਗਰੇਜ਼ੀ, ਹਿੰਦੀ ਜਾਂ ਜਿਸ ਭਾਸ਼ਾ ਵਿੱਚ ਚਾਹੋ ਬੋਲ ਸਕਦੇ ਹੋ। ਤੁਹਾਨੂੰ ਕੀ ਚਾਹੀਦਾ ਹੈ?",
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
