#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>

// 称重传感器模拟输入脚，实际测量时从这里读取 ADC。
#define ADC_PIN 34

// WiFi 信息：ESP32 上电后会连接这个热点。
const char* ssid = "hajimi";
const char* password = "123456789";

// MQTT 服务器地址：填运行 Mosquitto 的电脑 IP。
const char* mqtt_server = "172.20.10.4";
  
WiFiClient espClient;
PubSubClient client(espClient);

// 设备 ID：PC 服务端用它区分不同猫碗设备。
String deviceId = "cat_bowl_01";

unsigned long lastSendTime = 0;
unsigned long sampleInterval = 5000;  // 默认 5 秒上传一次

// 低粮报警阈值，单位 kg。
float alarmThresholdKg = 0.15;

// 简单线性校准参数：实际使用时按空碗和已知重量重新标定。
float zeroAdc = 1200;
float calibrationK = 0.0005;

/*
实际测量说明：
1. 空碗时读取 ADC，作为 zeroAdc。
2. 放入已知重量的猫粮，比如 0.5kg，读取 ADC，作为 knownWeightAdc。
3. 用下面公式计算 calibrationK：

   calibrationK = knownWeightKg / (knownWeightAdc - zeroAdc);

示例：

float zeroAdc = 1180;          // 空碗实测 ADC
float knownWeightAdc = 2180;   // 放入 0.5kg 后实测 ADC
float knownWeightKg = 0.5;
float calibrationK = knownWeightKg / (knownWeightAdc - zeroAdc);

如果重量变化方向相反，比如放重物后 ADC 变小，则公式改成：

   calibrationK = knownWeightKg / (zeroAdc - knownWeightAdc);
   weight = (zeroAdc - raw) * calibrationK;
*/

// 连接 WiFi，成功后打印 ESP32 的局域网 IP。
void setup_wifi() {
  Serial.println();
  Serial.print("Connecting to WiFi: ");
  Serial.println(ssid);

  WiFi.begin(ssid, password);

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println();
  Serial.println("WiFi connected");
  Serial.print("ESP32 IP: ");
  Serial.println(WiFi.localIP());
}

// 当前是模拟数据版本：用 fakeRawAdc 模拟猫粮重量逐渐减少。
float readAverageAdc() {
  static float fakeRawAdc = 2200;

  // 模拟猫粮逐渐减少。
  fakeRawAdc -= random(3, 15);

  // 低于下限时，模拟重新加满猫粮。
  if (fakeRawAdc < 1150) {
    fakeRawAdc = 2200;
    Serial.println("[SIM] Cat food refilled");
  }

  return fakeRawAdc;
}

/*
实际 ADC 采样版本：
把上面的 readAverageAdc() 函数整体注释掉，再取消下面这段注释。
这个版本会从 ADC_PIN 真实读取多次 ADC，然后取平均值，减少抖动。

float readAverageAdc() {
  const int sampleCount = 20;
  long sum = 0;

  for (int i = 0; i < sampleCount; i++) {
    sum += analogRead(ADC_PIN);
    delay(5);
  }

  return sum / (float)sampleCount;
}
*/

/*
如果实际传感器方向和当前公式相同，下面现有代码不用改：

float weight = (raw - zeroAdc) * calibrationK;

如果实际传感器方向相反，放猫粮后 ADC 变小，则 publishWeight() 和 readWeightKg()
里的重量公式都改成：

float weight = (zeroAdc - raw) * calibrationK;
*/

// 读取 ADC 并换算成 kg。负数会归零，避免页面显示异常值。
float readWeightKg() {
  float raw = readAverageAdc();

  float weight = (raw - zeroAdc) * calibrationK;

  if (weight < 0) {
    weight = 0;
  }

  return weight;
}

// 发布重量数据到 pet/weight/data，PC 服务端会保存并显示。
void publishWeight() {
  float raw = readAverageAdc();
  float weight = (raw - zeroAdc) * calibrationK;

  if (weight < 0) {
    weight = 0;
  }

  StaticJsonDocument<256> doc;

  doc["device_id"] = deviceId;
  doc["weight_kg"] = weight;
  doc["raw_adc"] = raw;
  doc["alarm_threshold_kg"] = alarmThresholdKg;

  char buffer[256];
  serializeJson(doc, buffer);

  client.publish("pet/weight/data", buffer);

  Serial.print("Publish: ");
  Serial.println(buffer);
}

// 发布设备状态到 pet/device/status，例如 online、config_updated、tare_done。
void publishStatus(const char* status) {
  StaticJsonDocument<256> doc;

  doc["device_id"] = deviceId;
  doc["status"] = status;
  doc["sample_interval_s"] = sampleInterval / 1000;
  doc["alarm_threshold_kg"] = alarmThresholdKg;

  char buffer[256];
  serializeJson(doc, buffer);

  client.publish("pet/device/status", buffer);

  Serial.print("Status: ");
  Serial.println(buffer);
}

// 处理 PC 端下发的 MQTT 控制命令。
void callback(char* topic, byte* payload, unsigned int length) {
  String message = "";

  for (int i = 0; i < length; i++) {
    message += (char)payload[i];
  }

  Serial.print("Message arrived [");
  Serial.print(topic);
  Serial.print("]: ");
  Serial.println(message);

  StaticJsonDocument<256> doc;
  DeserializationError error = deserializeJson(doc, message);

  if (error) {
    Serial.println("JSON parse failed");
    return;
  }

  // 只处理发给本设备或发给 all 的命令。
  String targetId = doc["device_id"] | "";

  if (targetId != deviceId && targetId != "all") {
    return;
  }

  String cmd = doc["cmd"] | "";

  // 修改采集间隔或报警阈值。
  if (cmd == "set_config") {
    if (doc.containsKey("sample_interval_s")) {
      int intervalS = doc["sample_interval_s"];
      sampleInterval = intervalS * 1000UL;
    }

    if (doc.containsKey("alarm_threshold_kg")) {
      alarmThresholdKg = doc["alarm_threshold_kg"];
    }

    publishStatus("config_updated");
  }

  // 清零：把当前 ADC 当作新的空碗基准值。
  if (cmd == "tare") {
    zeroAdc = readAverageAdc();
    publishStatus("tare_done");
  }
}

// MQTT 断线后自动重连，重连成功后重新订阅控制主题。
void reconnect() {
  while (!client.connected()) {
    Serial.print("Connecting to MQTT...");

    if (client.connect(deviceId.c_str())) {
      Serial.println("connected");

      client.subscribe("pet/device/control");

      publishStatus("online");
    } else {
      Serial.print("failed, rc=");
      Serial.print(client.state());
      Serial.println(" try again in 5 seconds");

      delay(5000);
    }
  }
}

void setup() {
  Serial.begin(115200);

  // ESP32 ADC 设置为 12 位，读数范围大约是 0-4095。
  analogReadResolution(12);

  // 用 ADC 噪声初始化随机数，方便模拟数据变化。
  randomSeed(analogRead(ADC_PIN));

  setup_wifi();

  client.setServer(mqtt_server, 1883);
  client.setCallback(callback);
}

void loop() {
  // 保持 MQTT 在线。
  if (!client.connected()) {
    reconnect();
  }

  client.loop();

  unsigned long now = millis();

  // 到达采样间隔后发布一次重量数据。
  if (now - lastSendTime > sampleInterval) {
    lastSendTime = now;
    publishWeight();
  }
}
