# Recording (message.wav)

Asterisk sirf ek format saaf chalata hai: **8000 Hz, mono, 16-bit WAV**.
File ka naam `message.wav`, jagah: `/var/lib/asterisk/sounds/message.wav`.

## Sabse asaan tareeka — dashboard se

Dashboard (http://127.0.0.1:8080) par apni mp3/wav recording upload karo —
wo sox se khud `message.wav` bana kar sahi jagah rakh deta hai.

## Haath se convert karna ho to

```bash
sox meri-recording.mp3 -r 8000 -c 1 /var/lib/asterisk/sounds/message.wav
```

Check: `soxi /var/lib/asterisk/sounds/message.wav` → Sample Rate 8000, Channels 1.

## Recording banate waqt

- **20–30 second** ki rakho — lambi recording = log pehle kaat dete hain,
  aur baat ke seconds bhi zyada bante hain.
- Pehle 2 second me hi saaf bolo ki call kisliye hai (naam/company + kaam).
- Apni asal awaz best hai; TTS bhi chalega par robot lagega.
- Gaana/background music mat rakho — telephone line par sirf awaz saaf jati hai.

Is folder me `uploads/` ke andar dashboard se upload hui original files rehti hain,
aur jab Asterisk sounds folder writable na ho to converted `message.wav` bhi yahin banta hai.
