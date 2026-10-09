<?php
// Форма «Баг или идея?» → письмо на почту автора. Отвечает JSON {ok, error}; без JS — редирект обратно.
const TO = 'n3ba000@gmail.com';                     // куда приходят сообщения
const FROM = 'mailsmoothcursor@smoothcursor.ru';    // от кого: ящик на домене сайта (создан в Beget)

// без ?? и str_contains: на хостинге может стоять старый PHP 5.6
function get($a, $k) { return isset($a[$k]) ? (string)$a[$k] : ''; }

function done($ok, $error = '') {
    if (strpos(get($_SERVER, 'HTTP_ACCEPT'), 'application/json') !== false) {
        header('Content-Type: application/json; charset=utf-8');
        http_response_code($ok ? 200 : 400);
        echo json_encode(['ok' => $ok, 'error' => $error]);
    } else {
        header('Location: ./?sent=' . ($ok ? '1' : '0') . '#bug');
    }
    exit;
}

if ($_SERVER['REQUEST_METHOD'] !== 'POST') done(false, 'method');
if (get($_POST, 'website') !== '') done(true);  // ловушка для ботов: людям поле не видно

$msg = trim(get($_POST, 'message'));
$email = trim(get($_POST, 'email'));
$len = preg_match_all('/./us', $msg);  // символы, а не байты; false — битый UTF-8
if (!$len || $len < 10 || $len > 5000) done(false, 'length');
if ($email !== '' && !filter_var($email, FILTER_VALIDATE_EMAIL)) done(false, 'email');

// ponytail: не чаще раза в минуту с одного IP через файл во временной папке; при наплыве спама — капча
$ip = get($_SERVER, 'REMOTE_ADDR');
$stamp = sys_get_temp_dir() . '/sc_bug_' . md5($ip);
if (is_file($stamp) && time() - filemtime($stamp) < 60) done(false, 'often');
touch($stamp);

$headers = [
    'From: Smooth Cursor <' . FROM . '>',
    'Content-Type: text/plain; charset=utf-8',
    'MIME-Version: 1.0',
];
if ($email !== '') $headers[] = 'Reply-To: ' . $email;  // filter_var выше не пропускает переводы строк
$body = $msg . "\n\n---\nОт: " . ($email ?: 'не указан') . "\nIP: $ip\nБраузер: " . (get($_SERVER, 'HTTP_USER_AGENT'));
$subject = '=?UTF-8?B?' . base64_encode('Smooth Cursor: сообщение с сайта') . '?=';

done(mail(TO, $subject, $body, implode("\r\n", $headers), '-f' . FROM), 'send');
