// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#include "ayu/ui/boxes/plugin_settings_box.h"

#include "ayu/plugins/bridge/python_bridge.h"
#include "ayu/plugins/event_bus.h"
#include "ayu/plugins/plugins_manager.h"
#include "crl/crl_async.h"
#include "rpl/rpl.h"
#include "base/unique_qptr.h"
#include "platform/platform_file_utilities.h"
#include "lang_auto.h"
#include "settings/settings_common.h"
#include "styles/style_ayu_styles.h"
#include "styles/style_boxes.h"
#include "styles/style_layers.h"
#include "styles/style_settings.h"
#include "ui/effects/animations.h"
#include "ui/effects/panel_animation.h"
#include "ui/boxes/confirm_box.h"
#include "ui/layers/generic_box.h"
#include "ui/text/text_entity.h"
#include "ui/widgets/buttons.h"
#include "ui/widgets/checkbox.h"
#include "ui/vertical_list.h"
#include "ui/widgets/fields/input_field.h"
#include "ui/widgets/labels.h"
#include "ui/widgets/dropdown_menu.h"
#include "window/window_session_controller.h"

#include <QtCore/QJsonArray>
#include <QtCore/QJsonDocument>
#include <QtCore/QJsonObject>

#include <functional>
#include <vector>

namespace Ayu::Plugins::SettingsBox {
namespace {

// A declared row reduced to what the dialog needs. The controls are kept as
// plain readers instead of widget pointers: the widgets belong to the box and
// die with it, while a std::function that captures them is only ever invoked
// while the box is still open.
struct Row {
	QString key;
	QString type;
	QJsonValue value;
	std::function<bool()> readBool;
	std::function<QString()> readText;
	// A selector's options, so the label on the button can be turned back into
	// the index the plugin declared. Without it the only thing that can be saved
	// is the text the user saw.
	QStringList choiceItems;
};

constexpr auto kMaxSettingLength = 512;

// What a selector row is showing right now. Kept beside the producer that
// repaints the button, so picking an item and pressing save always agree.
struct ChoiceState {
	QString value;
};

QJsonValue CurrentValue(const QJsonObject &stored, const Row &row) {
	if (!row.key.isEmpty() && stored.contains(row.key)) {
		return stored.value(row.key);
	}
	return row.value;
}

void AddLabel(
		not_null<Ui::GenericBox*> box,
		const QString &text,
		const style::FlatLabel &style) {
	// Выравнивание по левому краю, а не по центру.
	//
	// По центру подписи шли вразнобой - заголовок раздела, подпись
	// параметра, примечание - и список читался как случайный набор строк.
	// Слева общий край, по которому глаз идёт сверху вниз.
	//
	// setBreakEverywhere разрешает перенос по словам. Без него длинная
	// подпись настройки обрезалась по краю окна прямо посреди слова, и
	// прочитать её было нельзя.
	auto owned = object_ptr<Ui::FlatLabel>(box, rpl::single(text), style);
	const auto label = owned.get();
	label->setBreakEverywhere(true);
	box->addRow<Ui::FlatLabel>(std::move(owned));
}

void ApplyToPlugin(const QString &pluginId, const QJsonObject &values) {
	// Writing the file only changes what the plugin will read next time it
	// starts. Applying has to reach into the running instance, and that call
	// takes the interpreter lock, so it goes to a worker: doing it on the main
	// thread would hold the lock while the window waits for it.
	crl::async([pluginId, values] {
		Bridge::ApplyPluginSettings(pluginId, values);
		for (const auto &key : values.keys()) {
			EventBus::instance().emitSettingChanged(
				pluginId + u":"_q + key,
				values.value(key).toVariant().toString());
		}
	});
}

int ChoiceIndex(const QStringList &items, const QString &label) {
	// The label on the button is what the widget hands back, so the index has to
	// be recovered from it. A value that is already an index — because it was
	// written correctly, or by the CLI — is passed through instead of being
	// looked up among the labels, so a plugin whose items read as numbers keeps
	// working.
	for (int i = 0; i != items.size(); ++i) {
		if (items.at(i) == label) {
			return i;
		}
	}
	bool numeric = false;
	const auto asNumber = label.toInt(&numeric);
	return numeric ? asNumber : 0;
}

QString DescribeChoice(const QJsonObject &row, const QJsonValue &value) {
	auto items = QStringList();
	for (const auto &entry : row.value(u"items"_q).toArray()) {
		items.append(entry.toString());
	}
	const auto index = value.toInt();
	if (index < 0 || index >= items.size()) {
		return QString::number(index);
	}
	return items.value(index);
}

void FillSettingsBox(
		not_null<Ui::GenericBox*> box,
		not_null<Window::SessionController*> controller,
		const QString &pluginId,
		const QJsonArray &rows,
		const QJsonObject &stored) {
	const auto layout = box->verticalLayout();
	auto applied = std::make_shared<std::vector<Row>>();
	applied->reserve(rows.size());

	// Ширина задаётся до того, как созданы строки.
	//
	// Метки настроек при создании считают свою ширину от ширины окна, и
	// если окно на этот момент ещё стандартное, они выходят за его край и
	// текст обрезается слева. Поэтому ширина ставится первой, а в конце
	// только пересчитывается раскладка под неё.
	box->setWidth(st::aboutWidth);

	for (const auto &entry : rows) {
		const auto declared = entry.toObject();
		const auto type = declared.value(u"type"_q).toString();
		const auto key = declared.value(u"key"_q).toString();
		auto label = declared.value(u"label"_q).toString();
		if (label.isEmpty()) {
			label = declared.value(u"text"_q).toString();
		}
		auto row = Row();
		row.key = key;
		row.type = type;
		row.value = declared.value(u"default"_q);
		const auto current = CurrentValue(stored, row);

		if (type == u"header"_q) {
			AddLabel(box, label, st::defaultSubsectionTitle);
			continue;
		}
		if (type == u"note"_q) {
			AddLabel(box, label, st::pluginVersionAuthorLabel);
			continue;
		}
if (type == u"choice"_q) {
	// A selector the user can actually change. It used to be printed as
	// read-only text, which left every plugin with a Selector effectively
	// unconfigurable: the value was visible and nothing could move it.
		//
		// A link button rather than a settings button on purpose: the settings
		// button's label is bound to an rpl producer that this dialog cannot
		// keep alive, so the caption would freeze on the first value while the
		// plugin had in fact changed. LinkButton::setText is a plain setter.
		const auto items = declared.value(u"items"_q).toArray();
		if (key.isEmpty() || items.isEmpty()) {
			AddLabel(box, label, st::defaultSubsectionTitle);
			continue;
		}
		const auto choice = std::make_shared<ChoiceState>();
		choice->value = current.isString()
			? current.toString()
			: QString::number(current.toDouble());
		// Kept on the row so saving can turn this label back into the index the
		// plugin declared. An existing setting written as the label itself, by an
		// older build or by hand, has to keep displaying sensibly rather than
		// snapping to the first option.
		auto labels = QStringList();
		for (const auto &entry : items) {
			labels.append(entry.toString());
		}
		if (!labels.contains(choice->value)) {
			bool numeric = false;
			choice->value.toInt(&numeric);
			if (numeric) {
				const auto asNumber = choice->value.toInt();
				if (asNumber >= 0 && asNumber < labels.size()) {
					choice->value = labels.at(asNumber);
				}
			}
		}
		row.choiceItems = labels;
		AddLabel(box, label, st::defaultSubsectionTitle);
		auto owned = object_ptr<Ui::LinkButton>(
			box,
			choice->value,
			st::defaultLinkButton);
		// Raw pointer taken before the row takes ownership: addRow takes an
		// rvalue, so the object_ptr is empty afterwards.
		const auto link = owned.get();
		box->addRow(std::move(owned), st::boxRowPadding);
		link->setClickedCallback([=] {
			auto menu = base::make_unique_q<Ui::DropdownMenu>(
				box->window(),
				st::dropdownMenuWithIcons);
			for (const auto &entry : items) {
				const auto option = entry.toString();
				menu->addAction(option, [=] {
					choice->value = option;
					link->setText(option);
				});
			}
			const auto geometry = link->geometry();
			menu->move(
				geometry.x(),
				geometry.y() + geometry.height());
			menu->showAnimated(Ui::PanelAnimation::Origin::TopLeft);
		});
		row.readText = [choice] {
			return choice->value;
		};
		applied->push_back(std::move(row));
		continue;
	}
		if (key.isEmpty()) {
			continue;
		}

		if (type == u"bool"_q) {
			const auto button = Settings::AddButtonWithLabel(
				layout,
				rpl::single(label),
				rpl::single(label),
				st::settingsButtonNoIcon);
			const auto [toggle, checkView] = Settings::AddSeparatedToggle(
				button,
				st::settingsButtonNoIcon,
				current.toBool());
			toggle->clicks(
			) | rpl::on_next([=] {
				checkView->setChecked(
					!checkView->checked(),
					anim::type::normal);
			}, toggle->lifetime());
			const auto view = checkView;
			row.readBool = [view] {
				return view->checked();
			};
			applied->push_back(std::move(row));
			continue;
		}

		const auto text = current.isString()
			? current.toString()
			: QString::number(current.toDouble());
		AddLabel(box, label, st::defaultSubsectionTitle);
		const auto field = box->addRow(
			object_ptr<Ui::InputField>(
				box,
				st::defaultInputField,
				Ui::InputField::Mode::SingleLine,
				rpl::single(label),
				TextWithTags{ text }),
			st::boxRowPadding);
		field->setMaxLength(kMaxSettingLength);
		const auto input = field;
		row.readText = [input] {
			return input->getLastText();
		};
		applied->push_back(std::move(row));
	}

	box->addSkip(st::pluginChipSkip);
	// Раскладка пересчитывается под заданную в начале ширину: строки
	// создавались, когда окно ещё было стандартным.
	box->verticalLayout()->resizeToWidth(box->width());
	box->setMaxHeight(st::maxPluginSettingsHeight);

	box->addButton(tr::ayu_PluginSettingsSave(), [=] {
		auto out = QJsonObject();
		for (const auto &row : *applied) {
			if (row.readBool) {
				out.insert(row.key, row.readBool());
			} else if (row.readText) {
				const auto typed = row.readText();
				if (row.type == u"choice"_q) {
					// A selector is saved as its index, not as the label that
					// was on screen. Mobile plugins declare their choice with an
					// integer default and then subscript a list with it — advanced
					// spam does `GAME_SYMBOLS[get_setting("symbol", 0)]` — so
					// writing "0" instead of 0 raised KeyError in the hook and the
					// stored setting no longer matched the declared type at all.
					out.insert(row.key, ChoiceIndex(row.choiceItems, typed));
				} else {
					out.insert(row.key, (row.type == u"int"_q)
						? QJsonValue(typed.toInt())
						: QJsonValue(typed));
				}
			}
		}
		if (!Manager::instance().writePluginSettings(pluginId, out)) {
			controller->showToast(
				tr::ayu_PluginSettingsFailed(tr::now));
			return;
		}
		ApplyToPlugin(pluginId, out);
		box->closeBox();
		controller->showToast(tr::ayu_PluginSettingsSaved(tr::now));
	});
	box->addButton(tr::lng_cancel(), [=] {
		box->closeBox();
	});
}

void FillEmptySettingsBox(
		not_null<Ui::GenericBox*> box,
		const QString &pluginId) {
	box->addRow(
		object_ptr<Ui::FlatLabel>(
			box,
			tr::ayu_PluginSettingsNothingDeclared(tr::now),
			st::pluginVersionAuthorLabel),
		st::boxRowPadding);
	const auto path = Manager::instance().pluginSettingsPath(pluginId);
	box->addButton(tr::ayu_PluginSettingsOpenFile(), [=] {
		Platform::File::UnsafeLaunch(path);
	});
	box->addButton(tr::lng_cancel(), [=] {
		box->closeBox();
	});
}

} // namespace

void ShowPluginSettingsBox(
	not_null<Window::SessionController*> controller,
	const QString &pluginId) {
	const auto &manager = Manager::instance();
	auto rows = QJsonArray();
	const auto parsed = QJsonDocument::fromJson(
		manager.pluginSettingsSchema(pluginId));
	if (parsed.isObject()) {
		const auto encoded = parsed.object()
			.value(u"rows"_q)
			.toString()
			.toUtf8();
		if (!encoded.isEmpty()) {
			rows = QJsonDocument::fromJson(encoded).array();
		}
	}
	if (rows.isEmpty()) {
		controller->show(Box(
			FillEmptySettingsBox,
			pluginId));
		return;
	}
	controller->show(Box(
		FillSettingsBox,
		controller,
		pluginId,
		rows,
		manager.pluginSettingsValues(pluginId)));
}

} // namespace Ayu::Plugins::SettingsBox