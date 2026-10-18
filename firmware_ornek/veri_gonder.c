/*
 * Oskilo — STM32 tarafı örnek kod (HAL), metin biçimi
 * ====================================================
 *
 * Akış:
 *   1) ADC, bir timer tetiklemesiyle SABİT hızda örnekler (örn. 1 kHz).
 *   2) Örnekler DMA ile RAM'deki tampona yazılır.
 *   3) Tampon dolunca deney biter, veri UART'tan bilgisayara gönderilir:
 *
 *        START,4000          <- kaç satır geleceğini bildirir
 *        0,2048              <- sıra no, değer
 *        1,2051
 *        ...
 *        3999,2040
 *        END,4000            <- aktarım bitti
 *
 *   Uygulama sıra numarası ve START/END sayesinde tek bir satır bile
 *   kaybolsa yakalar ve ekranda kırmızıyla gösterir.
 *
 * CubeMX ayarları (özet):
 *   - TIM2: Prescaler/Period ile 1 kHz, Trigger Event Selection = Update Event (TRGO)
 *   - ADC1: Continuous Conversion = Disable
 *           External Trigger = Timer 2 Trigger Out event
 *           DMA Continuous Requests = Disable
 *   - DMA (ADC1): Mode = NORMAL  (!!! Circular OLMASIN — yoksa tampon dolunca
 *                 başa dönüp eski verinin üzerine yazar, veriler "kayar")
 *                 Data Width = Half Word
 *   - USART2: 115200 (ya da 921600) baud, 8N1
 *     Uygulamadaki "Baud hızı" ile AYNI olmalı.
 *
 * Örnekleme frekansını uygulamadaki "Örnekleme frekansı" kutusuna yazarsanız
 * Excel'de her örneğin zamanı (s) da hesaplanır.
 */

#include "main.h"
#include <stdio.h>
#include <string.h>

#define N_SAMPLES   4000u           /* tampon boyu: RAM'e sığacak kadar */

extern ADC_HandleTypeDef  hadc1;
extern TIM_HandleTypeDef  htim2;
extern UART_HandleTypeDef huart2;

static uint16_t adc_buf[N_SAMPLES];
static volatile uint8_t deney_bitti = 0;

/* ---------------------------------------------------------------- deney */
void Deney_Baslat(void)
{
    deney_bitti = 0;
    HAL_ADC_Start_DMA(&hadc1, (uint32_t *)adc_buf, N_SAMPLES);
    HAL_TIM_Base_Start(&htim2);              /* örneklemeyi timer başlatır */
}

/* DMA tampon dolunca HAL bu fonksiyonu çağırır */
void HAL_ADC_ConvCpltCallback(ADC_HandleTypeDef *hadc)
{
    if (hadc->Instance == ADC1) {
        HAL_TIM_Base_Stop(&htim2);
        deney_bitti = 1;                     /* gönderimi ana döngü yapar */
    }
}

/* ---------------------------------------------------------------- gönderim */
static void uart_yaz(const char *s, int len)
{
    HAL_UART_Transmit(&huart2, (uint8_t *)s, (uint16_t)len, HAL_MAX_DELAY);
}

void Veriyi_Gonder(void)
{
    /* Gönderim sırasında tampon değişmesin diye ADC/DMA durdurulmuş olmalı */
    char satir[40];
    int n;

    n = snprintf(satir, sizeof satir, "START,%u\r\n", (unsigned)N_SAMPLES);
    uart_yaz(satir, n);

    for (uint32_t i = 0; i < N_SAMPLES; i++) {
        /* Birden çok kanal varsa: "%lu,%u,%u\r\n" şeklinde virgülle ekleyin */
        n = snprintf(satir, sizeof satir, "%lu,%u\r\n", (unsigned long)i, adc_buf[i]);
        uart_yaz(satir, n);
    }

    n = snprintf(satir, sizeof satir, "END,%u\r\n", (unsigned)N_SAMPLES);
    uart_yaz(satir, n);
}

/* ---------------------------------------------------------------- main() içinde
 *
 *   Deney_Baslat();
 *   while (1)
 *   {
 *       if (deney_bitti) {
 *           deney_bitti = 0;
 *           HAL_ADC_Stop_DMA(&hadc1);
 *           Veriyi_Gonder();
 *       }
 *   }
 *
 * İPUÇLARI
 *  - Veri büyükse baud'u 921600 yapın (ST-Link sanal COM portu destekler);
 *    4000 satır (~45 KB) 115200 baud ile ~4 sn, 921600 ile ~0.5 sn sürer.
 *  - Uygulamadaki "komut gönder" kutusundan "GONDER" yazıp aynı veriyi
 *    tekrar istemek isterseniz UART RX kesmesiyle bu komutu yakalayıp
 *    Veriyi_Gonder() çağırabilirsiniz.
 *  - Ondalıklı değer göndermek isterseniz printf'in float desteği
 *    (-u _printf_float) gerekir; daha hafif yol: değeri 100 ile çarpıp
 *    tamsayı gönderin (25.31 °C -> 2531).
 */
