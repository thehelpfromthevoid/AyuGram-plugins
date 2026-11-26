// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#pragma once

#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <limits>
#include <string>
#include <system_error>

// Запись владения портом, которую клиент кладёт рядом со своим рабочим
// каталогом, когда поднимает прокси, и читает обратно, прежде чем
// переиспользовать слушателя SOCKS5, поднятого не в этом процессе.
//
// Нужна потому, что "порт отвечает SOCKS5" не значит "порт наш": любой
// локальный SOCKS5-прокси отвечает тем же приветствием, и молчаливый увод
// клиента в чужой прокси - это обход, который ничего не обходит. Запись
// называет процесс, владеющий портом, поэтому клиент отличает собственный
// отцепившийся прокси (живой после падения клиента) от чужого сервиса и
// отказывает во втором случае вместо тихого переиспользования.
//
// Формат намеренно тривиален и не зависит от Qt, чтобы его можно было
// проверить отдельно: две строки `key=value`, `pid` и `port`.
namespace Ayu::Dpi::State {

struct Record {
	std::int64_t pid = 0;
	std::uint16_t port = 0;
	bool valid = false;
};

// Строгое целое: только цифры и необязательный знак. Мусор не превращается в
// ноль, потому что ноль здесь означал бы "владельца нет".
inline bool ParseInt(const std::string &text, std::int64_t *out) {
	if (text.empty()) {
		return false;
	}
	auto index = std::size_t(0);
	auto negative = false;
	if (text[0] == '+' || text[0] == '-') {
		negative = (text[0] == '-');
		index = 1;
	}
	if (index >= text.size()) {
		return false;
	}
	auto value = std::int64_t(0);
	constexpr auto kLimit = std::numeric_limits<std::int64_t>::max();
	for (; index < text.size(); ++index) {
		if (text[index] < '0' || text[index] > '9') {
			return false;
		}
		const auto digit = std::int64_t(text[index] - '0');
		// Отказ до умножения: переполнение знакового int64 - это UB, а не
		// большое число. Поля здесь - pid и port, поэтому цифры дальше предела
		// означают порчу файла, и это промах, а не зажим владельца.
		if (value > (kLimit - digit) / 10) {
			return false;
		}
		value = value * 10 + digit;
	}
	*out = negative ? -value : value;
	return true;
}

// Разбирает тело файла. `valid` истинно, только если нашлись и
// положительный pid, и ненулевой port: недописанный или чужой файл это
// промах, а не частичное совпадение.
inline Record Parse(const std::string &text) {
	auto record = Record();
	auto havePid = false;
	auto havePort = false;
	auto pos = std::size_t(0);
	while (pos <= text.size()) {
		const auto end = text.find('\n', pos);
		auto line = text.substr(
			pos,
			(end == std::string::npos ? text.size() : end) - pos);
		while (!line.empty() && (line.back() == '\r' || line.back() == ' ')) {
			line.pop_back();
		}
		const auto eq = line.find('=');
		if (eq != std::string::npos) {
			const auto key = line.substr(0, eq);
			const auto value = line.substr(eq + 1);
			if (key == "pid") {
				havePid = ParseInt(value, &record.pid);
			} else if (key == "port") {
				auto port = std::int64_t(0);
				if (ParseInt(value, &port) && port > 0 && port <= 65535) {
					record.port = static_cast<std::uint16_t>(port);
					havePort = true;
				}
			}
		}
		if (end == std::string::npos) {
			break;
		}
		pos = end + 1;
	}
	record.valid = havePid && havePort && record.pid > 0;
	return record;
}

inline bool Write(
		const std::filesystem::path &path,
		std::int64_t pid,
		std::uint16_t port) {
	auto file = std::ofstream(path, std::ios::binary | std::ios::trunc);
	if (!file) {
		return false;
	}
	file << "pid=" << pid << "\n" << "port=" << port << "\n";
	return static_cast<bool>(file);
}

inline std::string Read(const std::filesystem::path &path) {
	auto file = std::ifstream(path, std::ios::binary);
	if (!file) {
		return std::string();
	}
	return std::string(
		std::istreambuf_iterator<char>(file),
		std::istreambuf_iterator<char>());
}

inline bool Remove(const std::filesystem::path &path) {
	auto error = std::error_code();
	return std::filesystem::remove(path, error);
}

} // namespace Ayu::Dpi::State
