/**
 * oskilo_net.c — STM32 (HAL) + W5500 için Oskilo ağ kütüphanesi. Açıklama: oskilo_net.h, README.md
 *
 * W5500 soketleri:  0 DHCP · 1 keşif (UDP 5099) · 2 ve 3 TCP veri (5000) · 4 UDP veri (5000)
 * İki TCP soketi aynı portu dinler: kablo çekilip takıldığında kart eski bağlantının koptuğunu henüz
 * fark etmemiş olsa bile Oskilo'nun yeni bağlantısı boştaki sokete düşer, eskisi kapatılır.
 */
#include "oskilo_net.h"

#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "main.h"            /* CubeMX: HAL ve pin adları */
#include "wizchip_conf.h"
#include "socket.h"
#include "dhcp.h"

#if !defined(_WIZCHIP_) || (_WIZCHIP_ != W5500)
#error "ioLibrary W5500 icin ayarlanmali: proje ayarlarinda _WIZCHIP_=W5500 tanimlayin (README)."
#endif

extern SPI_HandleTypeDef OSKILO_SPI;

#define S_DHCP  0
#define S_DISC  1
#define S_TCP0  2
#define S_TCP1  3
#define S_UDP   4

#define SUB_TIMEOUT_MS   5000u
#define BLINK_MS         3000u

enum { A_WAIT_LINK, A_DHCP, A_DHCP_OK, A_AUTOIP };

static oskilo_net_config_t cfg;
static char     dev_id[9];
static uint8_t  mac[6];
static uint32_t id_hash;
static char     name_enc[OSKILO_NAME_MAX + 1];
static uint8_t  dhcp_buf[548];             /* DHCP mesajı (RIP_MSG) */

static int      addr_state = A_WAIT_LINK;
static int      link_up = -1;
static uint32_t t_link, t_dhcp_tick, t_dhcp_deadline;
static uint32_t autoip_seed;

static int      cur_tcp = -1;              /* veri gönderilen TCP soketi */
static int      sub_on;                    /* UDP abonesi var mı */
static uint8_t  sub_ip[4];
static uint16_t sub_port;
static uint32_t sub_t;

static uint32_t blink_until, blink_next;
static int      blink_state;

static char     cmd_line[96];
static uint16_t cmd_len;

/* ------------------------------------------------------------------ SPI geri çağırmaları */
static void cs_sel(void)   { HAL_GPIO_WritePin(OSKILO_CS_PORT, OSKILO_CS_PIN, GPIO_PIN_RESET); }
static void cs_desel(void) { HAL_GPIO_WritePin(OSKILO_CS_PORT, OSKILO_CS_PIN, GPIO_PIN_SET); }

static uint8_t spi_rb(void)
{
    uint8_t tx = 0xFF, rx = 0;
    HAL_SPI_TransmitReceive(&OSKILO_SPI, &tx, &rx, 1, 10);
    return rx;
}

static void spi_wb(uint8_t b) { HAL_SPI_Transmit(&OSKILO_SPI, &b, 1, 10); }
static void spi_rbuf(uint8_t *buf, uint16_t len) { HAL_SPI_Receive(&OSKILO_SPI, buf, len, 100); }
static void spi_wbuf(uint8_t *buf, uint16_t len) { HAL_SPI_Transmit(&OSKILO_SPI, buf, len, 100); }

/* ------------------------------------------------------------------ zayıf (weak) varsayılanlar */
__attribute__((weak)) void oskilo_net_on_command(const char *line) { (void)line; }
__attribute__((weak)) void oskilo_net_on_identify(int led_on) { (void)led_on; }
__attribute__((weak)) void oskilo_net_on_address(const uint8_t ip[4], int dhcp) { (void)ip; (void)dhcp; }

/* ------------------------------------------------------------------ ad: flash'ta saklama */
#define NAME_MAGIC 0x4E4B534FUL            /* "OSKN" */

typedef union {
    struct {
        uint32_t magic;
        uint32_t len;
        char     name[OSKILO_NAME_MAX + 1];
        uint32_t sum;
        uint32_t pad;
    } r;
    uint64_t dw[10];                       /* 80 bayt, 8 bayt hizalı (çift kelime yazımı) */
} name_rec_t;

static uint32_t name_sum(const char *s, uint32_t len)
{
    uint32_t h = 2166136261UL;             /* FNV-1a */
    for (uint32_t i = 0; i < len; i++) { h ^= (uint8_t)s[i]; h *= 16777619UL; }
    return h;
}

static void name_load(void)
{
#if OSKILO_NAME_IN_FLASH
    const name_rec_t *rec = (const name_rec_t *)OSKILO_NAME_FLASH_ADDR;
    if (rec->r.magic == NAME_MAGIC && rec->r.len <= OSKILO_NAME_MAX &&
        rec->r.sum == name_sum(rec->r.name, rec->r.len)) {
        memcpy(name_enc, rec->r.name, rec->r.len);
        name_enc[rec->r.len] = 0;
        return;
    }
#endif
    name_enc[0] = 0;
}

/* Adı flash'a yazar ve geri okuyup doğrular. 0: tamam, -1: yazılamadı. */
static int name_save(const char *name)
{
#if OSKILO_NAME_IN_FLASH
    name_rec_t rec;
    uint32_t err = 0;
    int ok = 1;
    memset(&rec, 0, sizeof rec);
    rec.r.magic = NAME_MAGIC;
    rec.r.len = (uint32_t)strlen(name);
    memcpy(rec.r.name, name, rec.r.len);
    rec.r.sum = name_sum(rec.r.name, rec.r.len);

    FLASH_EraseInitTypeDef er;
    memset(&er, 0, sizeof er);
    er.TypeErase = FLASH_TYPEERASE_PAGES;
    er.Page = OSKILO_NAME_FLASH_PAGE;
    er.NbPages = 1;
    HAL_FLASH_Unlock();
#if defined(FLASH_FLAG_ALL_ERRORS)
    __HAL_FLASH_CLEAR_FLAG(FLASH_FLAG_ALL_ERRORS);    /* önceki bir işlemden kalan hata bayrakları */
#elif defined(FLASH_FLAG_SR_ERRORS)
    __HAL_FLASH_CLEAR_FLAG(FLASH_FLAG_SR_ERRORS);
#endif
    if (HAL_FLASHEx_Erase(&er, &err) != HAL_OK) ok = 0;
    for (uint32_t i = 0; ok && i < sizeof rec.dw / sizeof rec.dw[0]; i++) {
        if (HAL_FLASH_Program(FLASH_TYPEPROGRAM_DOUBLEWORD, OSKILO_NAME_FLASH_ADDR + 8u * i, rec.dw[i]) != HAL_OK)
            ok = 0;
    }
    HAL_FLASH_Lock();
    if (!ok || memcmp((const void *)OSKILO_NAME_FLASH_ADDR, &rec, sizeof rec) != 0) return -1;
#else
    (void)name;
#endif
    return 0;
}

/* ------------------------------------------------------------------ yardımcılar */
static int has_ip(void)
{
    uint8_t ip[4];
    getSIPR(ip);
    return (ip[0] | ip[1] | ip[2] | ip[3]) != 0;
}

/* Boşluk, %, = ve kontrol karakterlerini %XX yapar (duyuruda alan değeri olarak). */
static int put_enc(char *out, int room, const char *s)
{
    int n = 0;
    if (!s) s = "";
    for (; *s && n < room - 4; s++) {
        uint8_t c = (uint8_t)*s;
        if (c <= 0x20 || c == '%' || c == '=' || c == 0x7F)
            n += snprintf(out + n, (size_t)(room - n), "%%%02X", c);
        else
            out[n++] = (char)c;
    }
    out[n] = 0;
    return n;
}

/* "anahtar=değer" alanını bulur (önünde boşluk olmalı). */
static int get_field(const char *msg, const char *key, char *out, int room)
{
    size_t kl = strlen(key);
    const char *p = msg;
    while ((p = strstr(p, key)) != NULL) {
        if ((p == msg || p[-1] == ' ') && p[kl] == '=') {
            p += kl + 1;
            int n = 0;
            while (*p && *p != ' ' && *p != '\r' && *p != '\n' && n < room - 1)
                out[n++] = *p++;
            out[n] = 0;
            return n;
        }
        p += kl;
    }
    out[0] = 0;
    return -1;
}

static void tcp_close_all(void)
{
    close(S_TCP0);
    close(S_TCP1);
    cur_tcp = -1;
}

static void address_changed(int dhcp)
{
    uint8_t ip[4];
    tcp_close_all();                       /* eski adresle kurulmuş bağlantılar geçersiz */
    getSIPR(ip);
    oskilo_net_on_address(ip, dhcp);
}

static void set_autoip(void)
{
    /* 169.254.1.0 – 169.254.254.255 (ilk ve son 256 adres ayrılmış) */
    uint8_t ip[4] = {169, 254, (uint8_t)(1u + (autoip_seed % 254u)), (uint8_t)(autoip_seed >> 8)};
    uint8_t sn[4] = {255, 255, 0, 0};
    uint8_t gw[4] = {0, 0, 0, 0};
    setSIPR(ip);
    setSUBR(sn);
    setGAR(gw);
    setIR(IR_CONFLICT);
    addr_state = A_AUTOIP;
    address_changed(0);
}

/* DHCP kütüphanesinin varsayılan geri çağırmaları IP değişince W5500'ü tümden sıfırlar (soket
   tampon boyutları ve zaman aşımı ayarları gider). Bunun yerine sadece adresi yazıyoruz. */
static void dhcp_assign(void)
{
    uint8_t a[4];
    getIPfromDHCP(a); setSIPR(a);
    getSNfromDHCP(a); setSUBR(a);
    getGWfromDHCP(a); setGAR(a);
}

static void dhcp_conflict(void) { }      /* adres başkasında: DHCP_run yeniden dener / zaman aşımı */

static void start_dhcp(uint32_t now)
{
    tcp_close_all();
    reg_dhcp_cbfunc(dhcp_assign, dhcp_assign, dhcp_conflict);
    DHCP_init(S_DHCP, dhcp_buf);           /* IP'yi sıfırlar, adres ister */
    addr_state = A_DHCP;
    t_dhcp_tick = now + 1000u;
    t_dhcp_deadline = now + OSKILO_DHCP_TIMEOUT_MS;
}

/* Paket tampondan büyükse kalanını atar; atıldıysa 1 döner (o paket yok sayılır). */
static int drain_rest(uint8_t sn)
{
    uint16_t rest = 0;
    int dropped = 0;
    getsockopt(sn, SO_REMAINSIZE, &rest);
    while (rest) {
        uint8_t junk[64], ip[4];
        uint16_t port;
        int32_t r = recvfrom(sn, junk, rest > sizeof junk ? sizeof junk : rest, ip, &port);
        if (r <= 0) break;
        dropped = 1;
        getsockopt(sn, SO_REMAINSIZE, &rest);
    }
    return dropped;
}

static void feed_commands(const uint8_t *b, int32_t n, int end_is_eol)
{
    for (int32_t i = 0; i < n; i++) {
        char c = (char)b[i];
        if (c == '\r') continue;
        if (c == '\n') {
            cmd_line[cmd_len] = 0;
            if (cmd_len) oskilo_net_on_command(cmd_line);
            cmd_len = 0;
        } else if (cmd_len < sizeof cmd_line - 1) {
            cmd_line[cmd_len++] = c;
        }
    }
    if (end_is_eol && cmd_len) {           /* UDP: paketin sonu satırın sonu sayılır */
        cmd_line[cmd_len] = 0;
        oskilo_net_on_command(cmd_line);
        cmd_len = 0;
    }
}

/* ------------------------------------------------------------------ keşif (UDP 5099) */
static void announce(uint8_t *to_ip, uint16_t to_port, const char *ack)
{
    char b[300];
    uint8_t ip[4], sn[4];
    int n;
    getSIPR(ip);
    getSUBR(sn);
    n = snprintf(b, sizeof b, "OSKILO 1 id=%s name=%s ip=%u.%u.%u.%u mask=%u.%u.%u.%u tcp=%u udp=%u fmt=%s mode=%s fw=",
                 dev_id, name_enc, ip[0], ip[1], ip[2], ip[3], sn[0], sn[1], sn[2], sn[3],
                 cfg.tcp_port, cfg.udp_port, cfg.fmt, cfg.mode);
    n += put_enc(b + n, (int)sizeof b - n - 8, cfg.fw);
    n += snprintf(b + n, sizeof b - (size_t)n, " hw=");
    n += put_enc(b + n, (int)sizeof b - n - 20, cfg.hw);
    if (ack) n += snprintf(b + n, sizeof b - (size_t)n, " ack=%s", ack);
    b[n++] = '\n';

    /* Soran bizim alt ağımızda değilse (ör. bilgisayar elle IP'ye ayarlı) tek adrese gönderemeyiz:
       yayın adresine gönder. Oskilo kartı görür ve "farklı ağ ayarı" uyarısı verir. */
    uint8_t dst[4];
    memcpy(dst, to_ip, 4);
    for (int i = 0; i < 4; i++) {
        if ((to_ip[i] & sn[i]) != (ip[i] & sn[i])) {
            memset(dst, 255, 4);
            break;
        }
    }
    sendto(S_DISC, (uint8_t *)b, (uint16_t)n, dst, to_port);
}

static void handle_discovery(uint32_t now)
{
    static char b[200];
    uint8_t ip[4];
    uint16_t port;
    char val[OSKILO_NAME_MAX + 2];

    if (getSn_SR(S_DISC) != SOCK_UDP) {
        socket(S_DISC, Sn_MR_UDP, OSKILO_DISCOVERY_PORT, 0);
        return;
    }
    if (getSn_RX_RSR(S_DISC) == 0) return;
    int32_t n = recvfrom(S_DISC, (uint8_t *)b, sizeof b - 1, ip, &port);
    if (n <= 0 || drain_rest(S_DISC)) return;
    b[n] = 0;
    if (!has_ip()) return;                 /* henüz adresimiz yok: cevap veremeyiz */

    if (strncmp(b, "OSKILO?", 7) == 0) {
        announce(ip, port, NULL);
    } else if (strncmp(b, "OSKILO NAME 1 ", 14) == 0) {
        if (get_field(b, "id", val, sizeof val) > 0 && strcmp(val, dev_id) == 0) {
            int len = get_field(b, "name", val, sizeof val);
            if (len >= 0 && len <= OSKILO_NAME_MAX) {
                if (strcmp(val, name_enc) == 0 || name_save(val) == 0) {
                    strcpy(name_enc, val);
                    announce(ip, port, "name");
                }                          /* yazılamadıysa cevap yok: Oskilo "değiştirilemedi" der */
            }
        }
    } else if (strncmp(b, "OSKILO BLINK 1 ", 15) == 0) {
        if (get_field(b, "id", val, sizeof val) > 0 && strcmp(val, dev_id) == 0) {
            blink_until = now + BLINK_MS;
            blink_next = now;
            announce(ip, port, "blink");
        }
    }
}

/* ------------------------------------------------------------------ veri: TCP 5000 */
static void handle_tcp(uint8_t sn)
{
    switch (getSn_SR(sn)) {
    case SOCK_ESTABLISHED:
        if (getSn_IR(sn) & Sn_IR_CON) {
            setSn_IR(sn, Sn_IR_CON);
            if (cur_tcp >= 0 && cur_tcp != sn)
                close((uint8_t)cur_tcp);   /* yeni bağlantı eskisinin yerini alır */
            cur_tcp = sn;
            cmd_len = 0;
        }
        {
            uint16_t avail = getSn_RX_RSR(sn);
            while (avail) {
                uint8_t b[64];
                int32_t r = recv(sn, b, avail > sizeof b ? sizeof b : avail);
                if (r <= 0) break;
                feed_commands(b, r, 0);
                avail = (uint16_t)(avail - (uint16_t)r);
            }
        }
        break;
    case SOCK_CLOSE_WAIT:                  /* Oskilo bağlantıyı kapattı */
        disconnect(sn);
        if (cur_tcp == sn) cur_tcp = -1;
        break;
    case SOCK_INIT:
        listen(sn);
        break;
    case SOCK_CLOSED:
        if (cur_tcp == sn) cur_tcp = -1;
        if (cfg.tcp_port && has_ip() && link_up == 1) {
            if (socket(sn, Sn_MR_TCP, cfg.tcp_port, SF_TCP_NODELAY) == sn)
                setSn_KPALVTR(sn, 2);      /* 10 sn'de bir "orada mısın" (kablo çekilirse anlaşılır) */
        }
        break;
    default:
        break;
    }
}

/* ------------------------------------------------------------------ veri: UDP 5000 abonelik */
static void handle_udp(uint32_t now)
{
    static uint8_t b[160];
    uint8_t ip[4];
    uint16_t port;

    if (sub_on && (uint32_t)(now - sub_t) > SUB_TIMEOUT_MS) sub_on = 0;
    if (!cfg.udp_port) return;
    if (getSn_SR(S_UDP) != SOCK_UDP) {
        socket(S_UDP, Sn_MR_UDP, cfg.udp_port, 0);
        return;
    }
    if (getSn_RX_RSR(S_UDP) == 0) return;
    int32_t n = recvfrom(S_UDP, b, sizeof b - 1, ip, &port);
    if (n <= 0 || drain_rest(S_UDP)) return;
    b[n] = 0;
    int same = sub_on && memcmp(ip, sub_ip, 4) == 0 && port == sub_port;
    if (strncmp((char *)b, "OSKILO SUB ", 11) == 0) {
        if (!same) {
            memcpy(sub_ip, ip, 4);
            sub_port = port;
            sub_on = 1;
            cmd_len = 0;
        }
        sub_t = now;
        static const char ok[] = "OSKILO SUBOK 1\n";
        sendto(S_UDP, (uint8_t *)ok, sizeof ok - 1, ip, port);
    } else if (strncmp((char *)b, "OSKILO UNSUB ", 13) == 0) {
        if (same) sub_on = 0;
    } else if (same) {
        sub_t = now;
        feed_commands(b, n, 1);
    }
}

/* ------------------------------------------------------------------ dış arayüz */
int oskilo_net_init(const oskilo_net_config_t *c)
{
    static uint8_t txsz[8] = {2, 2, 4, 4, 4, 0, 0, 0};   /* KB, toplam 16 */
    static uint8_t rxsz[8] = {2, 2, 4, 4, 4, 0, 0, 0};

    memset(&cfg, 0, sizeof cfg);
    if (c) cfg = *c;
    if (!cfg.fmt)  cfg.fmt = "text";
    if (!cfg.mode) cfg.mode = "live";
    if (!cfg.fw)   cfg.fw = "1.0.0";
    if (!cfg.hw)   cfg.hw = "STM32+W5500";
    if (!cfg.tcp_port) cfg.tcp_port = 5000;
    if (!cfg.udp_port) cfg.udp_port = 5000;

    /* Kimlik ve MAC: 96 bit seri numaradan (her işlemcide farklı) */
    uint32_t uid[3] = {HAL_GetUIDw0(), HAL_GetUIDw1(), HAL_GetUIDw2()};
    id_hash = name_sum((const char *)uid, sizeof uid);
    snprintf(dev_id, sizeof dev_id, "%08lX", (unsigned long)id_hash);
    mac[0] = 0x02;                         /* yerel yönetilen, tekil (unicast) adres */
    mac[1] = 0x4F;                         /* 'O' */
    mac[2] = (uint8_t)(id_hash >> 24);
    mac[3] = (uint8_t)(id_hash >> 16);
    mac[4] = (uint8_t)(id_hash >> 8);
    mac[5] = (uint8_t)id_hash;
    autoip_seed = id_hash ^ (id_hash >> 16);

    cs_desel();
#ifdef OSKILO_RST_PORT
    HAL_GPIO_WritePin(OSKILO_RST_PORT, OSKILO_RST_PIN, GPIO_PIN_RESET);
    HAL_Delay(2);
    HAL_GPIO_WritePin(OSKILO_RST_PORT, OSKILO_RST_PIN, GPIO_PIN_SET);
#endif
    HAL_Delay(60);                         /* W5500 açılış süresi */
    reg_wizchip_cs_cbfunc(cs_sel, cs_desel);
    reg_wizchip_spi_cbfunc(spi_rb, spi_wb);
    reg_wizchip_spiburst_cbfunc(spi_rbuf, spi_wbuf);

    if (getVERSIONR() != 0x04) return -1;  /* W5500 değil ya da SPI çalışmıyor */
    if (wizchip_init(txsz, rxsz) != 0) return -1;

    wiz_NetTimeout to = {.retry_cnt = 4, .time_100us = 2000};   /* 200 ms × 4: ARP/TCP ~1 sn */
    wizchip_settimeout(&to);
    setSHAR(mac);
    name_load();

    socket(S_DISC, Sn_MR_UDP, OSKILO_DISCOVERY_PORT, 0);
    if (cfg.udp_port) socket(S_UDP, Sn_MR_UDP, cfg.udp_port, 0);
    addr_state = A_WAIT_LINK;
    link_up = -1;
    t_link = HAL_GetTick();
    return 0;
}

void oskilo_net_poll(void)
{
    uint32_t now = HAL_GetTick();

    /* kablo takıldı / çıkarıldı */
    if ((int32_t)(now - t_link) >= 0) {
        t_link = now + 250u;
        int l = wizphy_getphylink() == PHY_LINK_ON;
        if (l != link_up) {
            link_up = l;
            if (l) {
                start_dhcp(now);
            } else {
                if (addr_state == A_DHCP || addr_state == A_DHCP_OK) DHCP_stop();
                addr_state = A_WAIT_LINK;
                tcp_close_all();
                sub_on = 0;
            }
        }
    }

    /* adres: DHCP, olmazsa 169.254.x.x */
    if (addr_state == A_DHCP || addr_state == A_DHCP_OK) {
        while ((int32_t)(now - t_dhcp_tick) >= 0) {
            DHCP_time_handler();
            t_dhcp_tick += 1000u;
        }
        uint8_t r = DHCP_run();
        if (r == DHCP_IP_ASSIGN || r == DHCP_IP_CHANGED || (r == DHCP_IP_LEASED && addr_state == A_DHCP)) {
            addr_state = A_DHCP_OK;
            address_changed(1);
        } else if (addr_state == A_DHCP && (r == DHCP_FAILED || (int32_t)(now - t_dhcp_deadline) >= 0)) {
            DHCP_stop();
            set_autoip();
        } else if (addr_state == A_DHCP_OK && r == DHCP_FAILED) {
            DHCP_stop();                    /* kira yenilenemedi */
            set_autoip();
        }
    } else if (addr_state == A_AUTOIP && (getIR() & IR_CONFLICT)) {
        autoip_seed = autoip_seed * 1103515245u + 12345u;   /* adres başkasında: başka bir tane seç */
        set_autoip();
    }

    handle_discovery(now);
    handle_tcp(S_TCP0);
    handle_tcp(S_TCP1);
    handle_udp(now);

    /* "Kartı göster": LED 3 sn yanıp söner */
    if (blink_until) {
        if ((int32_t)(now - blink_until) >= 0) {
            blink_until = 0;
            blink_state = 0;
            oskilo_net_on_identify(0);
        } else if ((int32_t)(now - blink_next) >= 0) {
            blink_next = now + 150u;
            blink_state = !blink_state;
            oskilo_net_on_identify(blink_state);
        }
    }
}

int oskilo_net_connected(void)
{
    return (cur_tcp >= 0 && getSn_SR((uint8_t)cur_tcp) == SOCK_ESTABLISHED) || sub_on;
}

int oskilo_net_send(const void *data, uint16_t len)
{
    int sent = 0;
    if (!len) return 0;
    if (cur_tcp >= 0 && getSn_SR((uint8_t)cur_tcp) == SOCK_ESTABLISHED) {
        if (getSn_TX_FSR((uint8_t)cur_tcp) >= len) {   /* sığmıyorsa bekleme: bu parça atlanır */
            /* Önceki paket W5500'den henüz çıkmadıysa send() SOCK_BUSY (0) döner: en fazla ~2 ms dene */
            uint32_t t0 = HAL_GetTick();
            int32_t r;
            do {
                r = send((uint8_t)cur_tcp, (uint8_t *)data, len);
            } while (r == SOCK_BUSY && (uint32_t)(HAL_GetTick() - t0) < 3u);
            if (r > 0) sent = (int)r;
        }
    }
    if (sub_on) {
        int32_t r = sendto(S_UDP, (uint8_t *)data, len, sub_ip, sub_port);
        if (r > 0) sent = (int)r;
    }
    return sent;
}

int oskilo_net_printf(const char *fmt, ...)
{
    char b[256];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(b, sizeof b, fmt, ap);
    va_end(ap);
    if (n <= 0) return 0;
    if (n >= (int)sizeof b) n = (int)sizeof b - 1;
    return oskilo_net_send(b, (uint16_t)n);
}

const char *oskilo_net_id(void) { return dev_id; }
void oskilo_net_get_ip(uint8_t ip[4]) { getSIPR(ip); }
int oskilo_net_link_up(void) { return link_up == 1; }
