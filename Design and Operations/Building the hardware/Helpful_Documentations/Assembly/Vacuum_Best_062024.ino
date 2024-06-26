// Works best with 0.75 per bottle

int vacuum = 26;
int odor_A = 47;
int odor_B = 37;
int final_valve = 39;

void setup() {
pinMode (26, OUTPUT);
pinMode (47, OUTPUT);
pinMode (37, OUTPUT);
pinMode (39, OUTPUT);
}

void loop() {
digitalWrite(odor_A, HIGH);
delay(5000);
digitalWrite(final_valve, HIGH);
delay(1000);
digitalWrite(final_valve, LOW);
delay(30); // switchTime1
digitalWrite(odor_A, LOW);
delay(0); //switchTime2
digitalWrite(vacuum, HIGH);
delay(20); //vacuumDelay
digitalWrite(final_valve, HIGH);
delay(110); //portValveCycleTime
digitalWrite(final_valve, LOW);
digitalWrite(odor_B, HIGH);
delay(10); //vacuumDuration
digitalWrite(vacuum, LOW);


delay(1000);
digitalWrite(final_valve, HIGH);
delay(1000);
digitalWrite(final_valve, LOW);
delay(30);
digitalWrite(odor_B, LOW);
delay(0);
digitalWrite(vacuum, HIGH);
delay(20);
digitalWrite(final_valve, HIGH);
delay(110);
digitalWrite(final_valve, LOW);
digitalWrite(odor_B, HIGH);
delay(10);
digitalWrite(vacuum, LOW);

delay(1000);
digitalWrite(final_valve, HIGH);
delay(1000);
digitalWrite(final_valve, LOW);
delay(30);
digitalWrite(odor_B, LOW);
delay(0);
digitalWrite(vacuum, HIGH);
delay(20);
digitalWrite(final_valve, HIGH);
delay(110);
digitalWrite(final_valve, LOW);
digitalWrite(odor_B, HIGH);
delay(10);
digitalWrite(vacuum, LOW);

delay(1000);
digitalWrite(final_valve, HIGH);
delay(1000);
digitalWrite(final_valve, LOW);
delay(30);
digitalWrite(odor_B, LOW);
delay(0);
digitalWrite(vacuum, HIGH);
delay(20);
digitalWrite(final_valve, HIGH);
delay(110);
digitalWrite(final_valve, LOW);
digitalWrite(odor_B, HIGH);
delay(10);
digitalWrite(vacuum, LOW);

delay(1000);
digitalWrite(final_valve, HIGH);
delay(1000);
digitalWrite(final_valve, LOW);
delay(30);
digitalWrite(odor_B, LOW);
delay(0);
digitalWrite(vacuum, HIGH);
delay(20);
digitalWrite(final_valve, HIGH);
delay(110);
digitalWrite(final_valve, LOW);
digitalWrite(odor_B, HIGH);
delay(10);
digitalWrite(vacuum, LOW);

delay(1000);
digitalWrite(final_valve, HIGH);
delay(1000);
digitalWrite(final_valve, LOW);
delay(30);
digitalWrite(odor_B, LOW);
delay(0);
digitalWrite(vacuum, HIGH);
delay(20);
digitalWrite(final_valve, HIGH);
delay(110);
digitalWrite(final_valve, LOW);
digitalWrite(odor_A, HIGH);
delay(10);
digitalWrite(vacuum, LOW);

digitalWrite(odor_A, LOW);
}
