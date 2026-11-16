#include <Arduino_RouterBridge.h>

struct Opening {
  unsigned long sequence;
  unsigned long at;
};
Opening pending[32];
int head = 0, count = 0;
unsigned long sequence = 0, lost = 0;
String session;

String pillboxSession(String candidate) {
  if (session.length() == 0) {
    session = candidate;
  }
  return session;
}

String pillboxPeek() {
  if (count == 0) {
    return String("0,0,0,") + String(lost);
  }
  Opening event = pending[head];
  return String(event.sequence) + ",1," + String((unsigned long)(millis() - event.at)) + "," + String(lost);
}

bool pillboxAck(String acknowledged) {
  if (count && String(pending[head].sequence) == acknowledged) {
    head = (head + 1) % 32;
    count--;
  }
  return true;
}

void rememberOpening() {
  sequence++;
  if (count == 32) {
    lost++;
    return;
  }
  pending[(head + count) % 32] = {sequence, millis()};
  count++;
}


const int trigPin = 8;
const int echoPin = 7;

const float threshold = 7.0;
const int requiredReadings = 3;

// -1 = Unknown, 0 = Closed, 1 = Open
int boxState = -1;
int previousReading = -1;
int consecutiveReadings = 0;

void setup() {
  Serial.begin(115200);
  Bridge.begin();
  Bridge.provide_safe("pillbox_session", pillboxSession);
  Bridge.provide_safe("pillbox_peek", pillboxPeek);
  Bridge.provide_safe("pillbox_ack", pillboxAck);
  pinMode(trigPin, OUTPUT);
  pinMode(echoPin, INPUT);
}

void loop() {
  digitalWrite(trigPin, LOW);
  delayMicroseconds(2);

  digitalWrite(trigPin, HIGH);
  delayMicroseconds(10);
  digitalWrite(trigPin, LOW);

  unsigned long duration =
      pulseIn(echoPin, HIGH, 30000);

  if (duration == 0) {
    Serial.println("NO_ECHO");
    consecutiveReadings = 0;
    delay(100);
    return;
  }

  float distance = duration * 0.0343 / 2;

  int currentReading = (distance > threshold) ? 1 : 0;

  if (currentReading == previousReading) {
    consecutiveReadings++;
  } else {
    consecutiveReadings = 1;
    previousReading = currentReading;
  }

  if (consecutiveReadings >= requiredReadings &&
      currentReading != boxState) {

    boxState = currentReading;

    if (boxState == 1) {
      Serial.println("BOX_OPEN");
      rememberOpening();
    } else {
      Serial.println("BOX_CLOSED");
    }
  }

  delay(100);
}
