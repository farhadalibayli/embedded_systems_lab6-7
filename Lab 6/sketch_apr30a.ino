#include <Servo.h>
#include <Stepper.h>

// ── Pin definitions ──────────────────────────────────────────
const int BUTTON1   = 5;
const int BUTTON2   = 6;
const int BUZZER    = 3;
const int SERVO_PIN = 13;

// 28BYJ-48 needs 2048 steps for one full revolution
// Pin order matches ULN2003 IN1-IN2-IN3-IN4
const int STEPS_PER_REV = 2048;
Stepper stepperMotor(STEPS_PER_REV, 8, 9, 10, 11);

Servo servo;

// ── Servo angles ─────────────────────────────────────────────
const int SERVO_CENTER = 90;
const int SERVO_P1     = 20;    // pointer left  → P1 wins
const int SERVO_P2     = 160;   // pointer right → P2 wins

// ── Stepper increments ───────────────────────────────────────
const int STEP_INCREMENT = 128;   // one notch per round win
const int STEP_VICTORY   = 2048;  // full victory spin

// ── Reaction timeout — prevents infinite hang on hardware fault
const unsigned long REACTION_TIMEOUT = 10000;

// ── Game state ───────────────────────────────────────────────
int p1Score    = 0;
int p2Score    = 0;
int stepperPos = 0;  // net position so reset can return to 0

// ── Helper: power off all stepper coils after a move ─────────
// Prevents coils staying energized between moves which can
// confuse direction on the next step() call
void stepperOff() {
    digitalWrite(8,  LOW);
    digitalWrite(9,  LOW);
    digitalWrite(10, LOW);
    digitalWrite(11, LOW);
}

// ============================================================
void setup() {
    pinMode(BUTTON1, INPUT_PULLUP);
    pinMode(BUTTON2, INPUT_PULLUP);

    pinMode(BUZZER, OUTPUT);
    digitalWrite(BUZZER, LOW);   // active HIGH — LOW = OFF at start

    servo.attach(SERVO_PIN);
    servo.write(SERVO_CENTER);

    stepperMotor.setSpeed(10);   // 10 RPM — safe for 28BYJ-48

    Serial.begin(9600);
}

// ============================================================
void loop() {
    if (Serial.available()) {
        String cmd = Serial.readStringUntil('\n');
        cmd.trim();

        if (cmd == "START") {
            runRound();
        } else if (cmd == "RESET") {
            resetGame();
        }
    }
}

// ── Buzzer helper ────────────────────────────────────────────
void buzz(int ms) {
    digitalWrite(BUZZER, HIGH);  // ON
    delay(ms);
    digitalWrite(BUZZER, LOW);   // OFF
}

// ============================================================
//  MAIN ROUND LOGIC
// ============================================================
void runRound() {

    // Random wait: 1–20 seconds as per spec
    long waitMs = random(1000, 20001);
    unsigned long waitStart = millis();

    // Watch for false starts during countdown
    // INPUT_PULLUP: LOW = pressed, HIGH = not pressed
    while (millis() - waitStart < (unsigned long)waitMs) {
        if (digitalRead(BUTTON1) == LOW) {
            Serial.println("P1_FALSE");
            awardWin(2);
            return;
        }
        if (digitalRead(BUTTON2) == LOW) {
            Serial.println("P2_FALSE");
            awardWin(1);
            return;
        }
    }

    // Fire buzzer and signal Python
    buzz(300);
    Serial.println("GO");

    unsigned long reactionStart = millis();

    while (true) {

        // Timeout guard
        if (millis() - reactionStart > REACTION_TIMEOUT) {
            Serial.println("TIMEOUT");
            return;
        }

        if (digitalRead(BUTTON1) == LOW) {
            unsigned long t = millis() - reactionStart;
            Serial.print("P1:");
            Serial.println(t);
            awardWin(1);
            return;
        }

        if (digitalRead(BUTTON2) == LOW) {
            unsigned long t = millis() - reactionStart;
            Serial.print("P2:");
            Serial.println(t);
            awardWin(2);
            return;
        }
    }
}

// ============================================================
//  AWARD WIN — servo + stepper + match check
// ============================================================
void awardWin(int player) {

    if (player == 1) {
        p1Score++;
        servo.write(SERVO_P1);
        stepperMotor.step(+STEP_INCREMENT);  // P1 wins → RIGHT
        stepperPos += STEP_INCREMENT;
        stepperOff();  // power off coils to prevent direction confusion
    } else {
        p2Score++;
        servo.write(SERVO_P2);
        stepperMotor.step(-STEP_INCREMENT);  // P2 wins → LEFT
        stepperPos -= STEP_INCREMENT;
        stepperOff();  // power off coils to prevent direction confusion
    }

    delay(300);  // let servo settle

    // Send match result BEFORE victory spin so Python receives
    // the message without motor blocking serial
    if (p1Score >= 3) {
        Serial.println("P1_MATCH");
        delay(100);
        stepperMotor.setSpeed(8);
        stepperMotor.step(2048);   // full 360° victory spin
        stepperOff();
        stepperMotor.setSpeed(10);
    } else if (p2Score >= 3) {
        Serial.println("P2_MATCH");
        delay(100);
        stepperMotor.setSpeed(8);
        stepperMotor.step(2048);   // full 360° victory spin
        stepperOff();
        stepperMotor.setSpeed(10);
    }
}

// ============================================================
//  RESET — return all hardware to neutral state
// ============================================================
void resetGame() {
    p1Score = 0;
    p2Score = 0;

    servo.write(SERVO_CENTER);

    // Return stepper to starting position
    stepperMotor.step(-stepperPos);
    stepperOff();
    stepperPos = 0;

    Serial.println("RESET_DONE");
}
