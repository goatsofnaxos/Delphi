int pin = 49;  // any function will see this variable

void setup() {
  pinMode(pin, OUTPUT);    // sets the digital pin as output
}

void loop() {
  digitalWrite(pin, HIGH); // sets the digital pin on
  delay(1000);            // wait
  digitalWrite(pin, LOW);  // sets the digital pin off
  delay(1000);            // wait
}
