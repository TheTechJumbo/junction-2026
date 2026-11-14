
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
    } else {
      Serial.println("BOX_CLOSED");
    }
  }

  delay(100);
}
