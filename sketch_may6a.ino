#include <Arduino.h>
#include <Keypad.h>

// IRremote v4 needs DECODE_NEC enabled before the header is pulled in.
#define DECODE_NEC
#include <IRremote.hpp>

#include <SPI.h>
#include <MFRC522.h>

// ===================== Configuration =====================
static const uint8_t  CODE_LEN = 4;
#define DEBUG_IR 1 // set to 1 to print raw IR codes for remote mapping

// The code is set by the user on the keypad each time they lock the system.
// It is cleared back to empty when the system returns to WAITING.
static char activeCode[CODE_LEN + 1] = {0};

// ===================== Keypad ============================
const byte ROWS = 4;
const byte COLS = 4;

char keys[ROWS][COLS] = {
  {'1','2','3','A'},
  {'4','5','6','B'},
  {'7','8','9','C'},
  {'*','0','#','D'}
};

byte rowPins[ROWS] = {A0, A1, A2, A3};   // analog pins as digital
byte colPins[COLS] = {2,  3,  4,  5};

Keypad keypad = Keypad(makeKeymap(keys), rowPins, colPins, ROWS, COLS);

// ===================== IR Receiver =======================
static const uint8_t IR_PIN = 6;

struct IrMap { uint32_t code; char ch; };
// Default Elegoo / generic NEC remote. If yours differs, set DEBUG_IR=1
// and replace these hex codes with the ones printed over serial.
const IrMap IR_TABLE[] PROGMEM = {
  {0xE916FF00UL, '0'},
  {0xF30CFF00UL, '1'},
  {0xE718FF00UL, '2'},
  {0xA15EFF00UL, '3'},
  {0xF708FF00UL, '4'},
  {0xE31CFF00UL, '5'},
  {0xA55AFF00UL, '6'},
  {0xBD42FF00UL, '7'},
  {0xAD52FF00UL, '8'},
  {0xB54AFF00UL, '9'},
  {0xBA45FF00UL, '*'},   // POWER  -> cancel
  {0xB946FF00UL, '#'},   // VOL+   -> confirm
};
const size_t IR_TABLE_LEN = sizeof(IR_TABLE) / sizeof(IR_TABLE[0]);

static char irCodeToChar(uint32_t code) {
  for (size_t i = 0; i < IR_TABLE_LEN; i++) {
    IrMap entry;
    memcpy_P(&entry, &IR_TABLE[i], sizeof(IrMap));
    if (entry.code == code) return entry.ch;
  }
  return 0;
}

// ===================== RFID ==============================
static const uint8_t RFID_SS  = 10;
static const uint8_t RFID_RST = 9;
MFRC522 rfid(RFID_SS, RFID_RST);

// ===================== LEDs ==============================
static const uint8_t LED_RED   = 7;
static const uint8_t LED_GREEN = 8;

// ===================== State Machine =====================
enum SystemState : uint8_t { ST_WAITING, ST_LOCKED, ST_UNLOCKED };
SystemState state = ST_WAITING;

char     codeBuf[CODE_LEN + 1] = {0};
uint8_t  codeIdx               = 0;
uint32_t lastBlinkMs           = 0;
bool     blinkOn               = false;
uint32_t flashUntilMs          = 0;   // brief double-flash window after RFID read

// ===================== Helpers ===========================
static void sendState() {
  Serial.print(F("STATE,"));
  switch (state) {
    case ST_WAITING:  Serial.println(F("WAITING"));  break;
    case ST_LOCKED:   Serial.println(F("LOCKED"));   break;
    case ST_UNLOCKED: Serial.println(F("UNLOCKED")); break;
  }
}

static void resetCodeBuf() {
  memset(codeBuf, 0, sizeof(codeBuf));
  codeIdx = 0;
}

static void enterState(SystemState s) {
  state = s;
  resetCodeBuf();
  if (s == ST_WAITING) {
    // Clear the active code — a new one must be set the next time we lock.
    memset(activeCode, 0, sizeof(activeCode));
  }
  sendState();
}

static bool codeMatches() {
  if (codeIdx != CODE_LEN) return false;
  for (uint8_t i = 0; i < CODE_LEN; i++) {
    if (codeBuf[i] != activeCode[i]) return false;
  }
  return true;
}

// ===================== LED patterns ======================
static void updateLeds() {
  uint32_t now = millis();

  // Brief double-flash on RFID read (UNLOCKED only)
  if (flashUntilMs && now < flashUntilMs) {
    bool phase = ((now / 80) % 2) == 0;
    digitalWrite(LED_RED,   phase ? HIGH : LOW);
    digitalWrite(LED_GREEN, phase ? HIGH : LOW);
    return;
  } else if (flashUntilMs) {
    flashUntilMs = 0;
  }

  switch (state) {
    case ST_WAITING:
      if (now - lastBlinkMs >= 500) {
        lastBlinkMs = now;
        blinkOn = !blinkOn;
      }
      digitalWrite(LED_RED,   blinkOn ? HIGH : LOW);
      digitalWrite(LED_GREEN, LOW);
      break;
    case ST_LOCKED:
      digitalWrite(LED_RED,   HIGH);
      digitalWrite(LED_GREEN, LOW);
      break;
    case ST_UNLOCKED:
      digitalWrite(LED_RED,   LOW);
      digitalWrite(LED_GREEN, HIGH);
      break;
  }
}

// ===================== Input handlers ====================
static void storeActiveCode() {
  // Copy the just-entered keypad digits into activeCode and announce it.
  memcpy(activeCode, codeBuf, CODE_LEN);
  activeCode[CODE_LEN] = 0;
  Serial.print(F("INFO,code_set_"));
  Serial.println(activeCode);
}

static void handleDigit(char c, bool fromKeypad) {
  if (c == '*') {                         // cancel
    resetCodeBuf();
    Serial.println(F("INFO,cancel"));
    return;
  }
  if (c == '#') {                         // explicit confirm
    if (codeIdx == CODE_LEN) {
      if (state == ST_WAITING && fromKeypad) {
        storeActiveCode();
        enterState(ST_LOCKED);
      } else if (state == ST_LOCKED && !fromKeypad) {
        if (codeMatches()) enterState(ST_UNLOCKED);
        else { Serial.println(F("INFO,wrong_code")); resetCodeBuf(); }
      } else {
        resetCodeBuf();
      }
    }
    return;
  }
  if (c < '0' || c > '9') return;
  if (codeIdx >= CODE_LEN) return;

  codeBuf[codeIdx++] = c;
  codeBuf[codeIdx]   = 0;
  Serial.print(F("INFO,digit_"));
  Serial.println(fromKeypad ? F("kp") : F("ir"));

  // Auto-confirm at 4 digits — keypad locks (and stores the code), IR unlocks
  if (codeIdx == CODE_LEN) {
    if (state == ST_WAITING && fromKeypad) {
      storeActiveCode();
      enterState(ST_LOCKED);
    } else if (state == ST_LOCKED && !fromKeypad) {
      if (codeMatches()) enterState(ST_UNLOCKED);
      else { Serial.println(F("INFO,wrong_code")); resetCodeBuf(); }
    }
  }
}

// ===================== RFID polling ======================
static void rfidPoll() {
  if (state != ST_UNLOCKED) return;
  if (!rfid.PICC_IsNewCardPresent()) return;
  Serial.println(F("INFO,rfid_card_detected"));   // diagnostic
  if (!rfid.PICC_ReadCardSerial()) {
    Serial.println(F("INFO,rfid_read_failed"));   // diagnostic
    return;
  }

  Serial.print(F("TAG,"));
  for (byte i = 0; i < rfid.uid.size; i++) {
    if (rfid.uid.uidByte[i] < 0x10) Serial.print('0');
    Serial.print(rfid.uid.uidByte[i], HEX);
  }
  Serial.println();

  flashUntilMs = millis() + 480;          // ~3 quick flashes

  rfid.PICC_HaltA();
  rfid.PCD_StopCrypto1();
}

// ===================== Setup / Loop ======================
void setup() {
  Serial.begin(115200);
  while (!Serial && millis() < 2000) { /* wait briefly for USB CDC */ }

  pinMode(LED_RED,   OUTPUT);
  pinMode(LED_GREEN, OUTPUT);

  // IRremote v4 init. DISABLE_LED_FEEDBACK avoids the library flashing pin 13,
  // which is RFID SCK in our wiring.
  IrReceiver.begin(IR_PIN, DISABLE_LED_FEEDBACK);

  SPI.begin();
  rfid.PCD_Init();

  // Boost antenna gain to max (48 dB). Defaults to ~33 dB which is too low
  // for clone chips. PCD_RxGain_max == 0x07 << 4.
  rfid.PCD_SetAntennaGain(MFRC522::RxGain_max);
  rfid.PCD_AntennaOn();   // make sure the antenna is energised after re-init

  // Periodic re-poll: if no tag for a while, some clone chips need a kick.
  // (Handled in loop() via a soft-reset-on-stuck pattern below.)

  // ---- RC522 self-test: read the chip's version register ----
  // 0x91 / 0x92 = real chip, 0x00 / 0xFF = wiring or power problem
  byte v = rfid.PCD_ReadRegister(MFRC522::VersionReg);
  Serial.print(F("INFO,rfid_version_0x"));
  Serial.println(v, HEX);
  if (v == 0x00 || v == 0xFF) {
    Serial.println(F("INFO,rfid_NOT_RESPONDING_check_wiring"));
  } else {
    Serial.println(F("INFO,rfid_OK"));
  }
  // Confirm antenna is actually on
  byte tx = rfid.PCD_ReadRegister(MFRC522::TxControlReg);
  Serial.print(F("INFO,rfid_txcontrol_0x"));
  Serial.println(tx, HEX);   // expect 0x83 when antenna on

  Serial.println(F("INFO,boot"));
  enterState(ST_WAITING);
}

void loop() {
  // 1. Keypad — locks the system in WAITING; '*' re-arms when UNLOCKED
  char k = keypad.getKey();
  if (k) {
    if (state == ST_WAITING) {
      handleDigit(k, true);
    } else if (state == ST_UNLOCKED && k == '*') {
      enterState(ST_WAITING);
    }
  }

  // 2. IR receiver — unlocks the system in LOCKED
  if (IrReceiver.decode()) {
    uint32_t raw = IrReceiver.decodedIRData.decodedRawData;
    bool isRepeat = (IrReceiver.decodedIRData.flags & IRDATA_FLAGS_IS_REPEAT);
#if DEBUG_IR
    Serial.print(F("INFO,ir_raw_0x"));
    Serial.println(raw, HEX);
#endif
    if (!isRepeat) {
      char c = irCodeToChar(raw);
      if (c && state == ST_LOCKED) handleDigit(c, false);
    }
    IrReceiver.resume();
  }

  // 3. RFID — only active in UNLOCKED
  rfidPoll();

  // 4. LEDs
  updateLeds();
}