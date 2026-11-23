// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mehmet Can Dusgun (mhmtcndsgn) — Oskilo örnek kart kodu
/**
 * main_ornek.c — CubeMX'in oluşturduğu main.c'ye eklenecek parçalar (NUCLEO-WL55JC1 + W5500)
 *
 * Bu dosya tek başına derlenmez: aşağıdaki blokları main.c'de aynı adlı USER CODE bölümlerine
 * kopyalayın. CubeMX yeniden kod üretse de USER CODE bölümleri korunur.
 *
 * Kart açılınca adres alır (DHCP ya da 169.254.x.x), Oskilo onu port listesinde
 * "🌐 Oskilo XXXX" olarak gösterir. Bağlanınca 50 Hz "sira,adc1,adc2" satırları gelir.
 */

/* USER CODE BEGIN Includes */
#include "oskilo_net.h"
#include <string.h>
/* USER CODE END Includes */

/* USER CODE BEGIN PV */
/* "Kartı göster" için LED. CubeMX'te kart seçerek proje açtıysanız LED1_Pin tanımlıdır;
   değilse NUCLEO-WL55JC1'de mavi LED PB15'tir (CubeMX'te GPIO_Output yapın). */
#if defined(LED1_Pin)
#define ID_LED_PORT LED1_GPIO_Port
#define ID_LED_PIN  LED1_Pin
#else
#define ID_LED_PORT GPIOB
#define ID_LED_PIN  GPIO_PIN_15
#endif

static uint32_t sira;          /* her satırda bir artar: Oskilo kayıp satırı buradan anlar */
static int gonder = 1;         /* "DURDUR" / "BASLA" komutlarıyla değişir */
/* USER CODE END PV */

/* USER CODE BEGIN 0 */
/* Oskilo'dan gelen komutlar (Oskilo'nun Terminal / hızlı komut düğmeleri) */
void oskilo_net_on_command(const char *line)
{
    if (strcmp(line, "DURDUR") == 0 || strcmp(line, "STOP") == 0) gonder = 0;
    else if (strcmp(line, "BASLA") == 0 || strcmp(line, "START") == 0) gonder = 1;
    else if (strcmp(line, "SIFIRLA") == 0 || strcmp(line, "RESET") == 0) sira = 0;
}

/* Oskilo'da "Kartı göster" düğmesine basılınca LED yanıp söner */
void oskilo_net_on_identify(int led_on)
{
    HAL_GPIO_WritePin(ID_LED_PORT, ID_LED_PIN, led_on ? GPIO_PIN_SET : GPIO_PIN_RESET);
}
/* USER CODE END 0 */

int main(void)
{
    /* ... CubeMX: HAL_Init(), SystemClock_Config(), MX_GPIO_Init(), MX_SPI1_Init() ... */

    /* USER CODE BEGIN 2 */
    static const oskilo_net_config_t ag = {
        .hw   = "NUCLEO-WL55JC1 + W5500",   /* Oskilo'da kart bilgisinde görünür */
        .fw   = "1.0.0",
        .fmt  = "text",                      /* Oskilo "Metin (satır)" seçili gelir */
        .mode = "live",                      /* Oskilo "Canlı izleme" seçili gelir */
        .tcp_port = 5000,
        .udp_port = 5000,
    };
    if (oskilo_net_init(&ag) != 0) {
        /* W5500 cevap vermiyor: SPI kabloları, 3.3V besleme ve CS pinini kontrol edin.
           Burada LED hızlı yanıp söner. */
        while (1) {
            HAL_GPIO_TogglePin(ID_LED_PORT, ID_LED_PIN);
            HAL_Delay(100);
        }
    }
    uint32_t sonraki = HAL_GetTick();
    /* USER CODE END 2 */

    /* USER CODE BEGIN WHILE */
    while (1) {
        oskilo_net_poll();                   /* ağ işleri: bekletmez, sık çağırın */

        if ((int32_t)(HAL_GetTick() - sonraki) >= 0) {
            sonraki += 20;                   /* 50 Hz */
            if (gonder && oskilo_net_connected()) {
                /* Örnek veri: iki üçgen dalga. Gerçek ölçüm için ADC değerlerinizi yazın,
                   ör. HAL_ADC_GetValue(&hadc). */
                uint16_t adc1 = (uint16_t)((sira * 37u) % 4096u);
                uint16_t adc2 = (uint16_t)(4095u - (sira * 11u) % 4096u);
                oskilo_net_printf("%lu,%u,%u\r\n", (unsigned long)sira, adc1, adc2);
                sira++;
            }
        }
        /* USER CODE END WHILE */

        /* USER CODE BEGIN 3 */
    }
    /* USER CODE END 3 */
}
