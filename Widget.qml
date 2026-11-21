pragma ComponentBehavior: Bound
// Panel layout and shared components adapted from phi0x/omarchy-sennheiser-control;
// see THIRD_PARTY.md.
import QtQuick
import QtQuick.Controls as Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Ui
import qs.Commons
import qs.Commons as Commons

Panel {
    id: root
    moduleName: "chadsbrown.accentum"
    ipcTarget: "chadsbrown.accentum"
    property var snapshot: ({
            headphones: {},
            devices: []
        })
    readonly property var hp: snapshot.headphones || ({})
    readonly property var values: hp.values || ({})
    readonly property var eq: snapshot.eq || ({})
    readonly property var presets: snapshot.presets || []
    readonly property var bands: snapshot.bands || []
    readonly property bool busy: !!snapshot.busy
    readonly property bool podcast: values.audio_mode === 2
    property string tab: "headphones"
    property bool started: false
    property string workerError: ""

    // Equalizer draft: what the sliders show. Copied from the headphones whenever
    // the backend reports a new reading, and sent only when Apply is pressed.
    property var draftGains: [0, 0, 0, 0, 0]
    property bool draftBass: false
    property int eqRevision: snapshot.eq_revision || 0
    onEqRevisionChanged: resetDraft()
    readonly property bool draftDirty: {
        let stored = eq.gains || [];
        if (draftBass !== !!eq.bass_boost)
            return true;
        for (let i = 0; i < draftGains.length; ++i)
            if (Math.abs(draftGains[i] - (stored[i] || 0)) > 0.01)
                return true;
        return false;
    }
    readonly property int draftPreset: {
        for (let p = 0; p < presets.length; ++p) {
            let same = true;
            for (let i = 0; i < draftGains.length; ++i)
                if (Math.abs(draftGains[i] - presets[p].gains[i]) > 0.01)
                    same = false;
            if (same)
                return p + 1;
        }
        return 0;
    }
    function resetDraft() {
        let stored = eq.gains || [0, 0, 0, 0, 0];
        draftGains = stored.slice();
        draftBass = !!eq.bass_boost;
    }
    function setDraftGain(index, value) {
        let next = draftGains.slice();
        next[index] = Math.round(value * 2) / 2;
        draftGains = next;
    }
    function formatGain(value) {
        return (value > 0 ? "+" : value < 0 ? "−" : "") + Math.abs(value).toFixed(1) + " dB";
    }

    implicitWidth: icon.implicitWidth
    implicitHeight: icon.implicitHeight
    onOpenedChanged: {
        if (opened && !started) {
            started = true;
            worker.running = true;
        } else if (opened)
            send({
                action: "refresh"
            });
    }
    onTabChanged: Qt.callLater(function () {
        if (settingsScroll.contentItem && settingsScroll.contentItem.contentY !== undefined)
            settingsScroll.contentItem.contentY = 0;
    })
    function send(request) {
        if (worker.running && !snapshot.busy) {
            worker.write(JSON.stringify(request) + "\n");
            workerError = "";
            return true;
        }
        return false;
    }
    function change(key, value) {
        send({
            action: "setting",
            key: key,
            value: value
        });
    }
    Process {
        id: worker
        command: ["/usr/bin/python", "-B", Qt.resolvedUrl("backend/client.py").toString().replace(/^file:\/\//, "")]
        stdinEnabled: true
        stdout: SplitParser {
            onRead: data => {
                try {
                    root.snapshot = JSON.parse(data);
                } catch (e) {
                    root.workerError = "Could not read controller response";
                }
            }
        }
        stderr: SplitParser {
            onRead: data => {
                root.workerError = data;
            }
        }
        onExited: (code, status) => {
            root.started = false;
            root.snapshot = {
                headphones: {},
                devices: []
            };
            root.workerError = "Controller stopped (" + code + "). Close and reopen to retry.";
        }
    }
    // omarchy-shell accentum-control showTab eq | status | set transparency 1
    IpcHandler {
        target: "accentum-control"
        function status(): string {
            return JSON.stringify(root.snapshot);
        }
        function showTab(name: string): void {
            root.tab = name === "eq" ? "eq" : "headphones";
            root.open();
        }
        function set(key: string, value: int): void {
            root.change(key, value);
        }
        function preset(name: string): void {
            for (let i = 0; i < root.presets.length; ++i)
                if (root.presets[i].name.toLowerCase() === name.toLowerCase())
                    root.send({
                        action: "eq_write",
                        gains: root.presets[i].gains,
                        bass_boost: !!root.eq.bass_boost
                    });
        }
    }
    BarIconButton {
        id: icon
        anchors.fill: parent
        bar: root.bar
        text: "󰋋"
        active: root.opened
        tooltipText: "Accentum" + (root.values.battery === undefined ? "" : " · " + root.values.battery + "%")
        onPressed: root.toggle()
    }
    component Copy: Text {
        color: Commons.Color.foreground
        font.family: Style.font.family
        font.pixelSize: 14
        textFormat: Text.PlainText
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    }
    component Hint: Copy {
        font.pixelSize: 12
        opacity: .65
    }
    component Action: Controls.Button {
        id: actionButton
        Layout.fillWidth: true
        implicitHeight: 38
        enabled: !root.busy
        opacity: enabled ? 1 : 0.5
        background: Rectangle {
            radius: 4
            color: actionButton.down || actionButton.checked ? Qt.alpha(Commons.Color.accent, 0.24) : Qt.alpha(Commons.Color.foreground, actionButton.hovered ? 0.14 : 0.07)
            border.width: actionButton.visualFocus ? 1 : 0
            border.color: Commons.Color.accent
        }
        contentItem: Text {
            text: actionButton.text
            textFormat: Text.PlainText
            color: Commons.Color.foreground
            font.family: Style.font.family
            font.pixelSize: 14
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
    }
    component Choice: Controls.ComboBox {
        id: choice
        Layout.fillWidth: true
        implicitHeight: 38
        opacity: enabled ? 1 : .4
        palette.text: Commons.Color.foreground
        palette.buttonText: Commons.Color.foreground
        palette.base: Commons.Color.popups.background
        palette.window: Commons.Color.popups.background
        palette.highlight: Commons.Color.accent
        background: Rectangle {
            radius: 4
            color: Qt.alpha(Commons.Color.foreground, 0.05)
            border.width: 1
            border.color: Qt.alpha(Commons.Color.foreground, choice.activeFocus ? 0.5 : 0.15)
        }
        contentItem: Text {
            text: choice.displayText
            textFormat: Text.PlainText
            color: Commons.Color.foreground
            font.family: Style.font.family
            font.pixelSize: 14
            leftPadding: 10
            rightPadding: 22
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
    }
    // A labelled dropdown bound to one headphone setting.
    component ValueSetting: ColumnLayout {
        id: valueSetting
        property string title
        property string hint: ""
        property string settingKey
        property var choices
        Layout.fillWidth: true
        spacing: 8
        visible: root.values[settingKey] !== undefined
        Copy {
            text: valueSetting.title
            font.bold: true
        }
        Choice {
            id: valueChoice
            model: valueSetting.choices
            textRole: "name"
            enabled: !root.busy
            onActivated: root.change(valueSetting.settingKey, valueSetting.choices[currentIndex].value)
            Binding {
                target: valueChoice
                property: "currentIndex"
                value: valueSetting.choices.findIndex(function (item) {
                    return item.value === root.values[valueSetting.settingKey];
                })
                restoreMode: Binding.RestoreNone
            }
        }
        Hint {
            visible: valueSetting.hint !== ""
            text: valueSetting.hint
        }
    }
    component OnOff: Controls.Switch {
        id: onOff
        opacity: enabled ? 1 : .4
        indicator: Rectangle {
            implicitWidth: 40
            implicitHeight: 22
            x: onOff.leftPadding
            y: onOff.topPadding + (onOff.availableHeight - height) / 2
            radius: height / 2
            color: onOff.checked ? Commons.Color.accent : Qt.alpha(Commons.Color.foreground, .18)
            border.width: onOff.visualFocus ? 1 : 0
            border.color: Commons.Color.foreground
            Rectangle {
                width: 16
                height: 16
                radius: 8
                y: 3
                x: onOff.checked ? parent.width - width - 3 : 3
                color: onOff.checked ? Commons.Color.popups.background : Commons.Color.foreground
            }
        }
    }
    // Slider whose fill grows out from the centre (0 dB) in either direction.
    component Fader: Controls.Slider {
        id: fader
        implicitHeight: 28
        opacity: enabled ? 1 : .4
        background: Rectangle {
            x: fader.leftPadding
            y: fader.topPadding + (fader.availableHeight - height) / 2
            width: fader.availableWidth
            height: 4
            radius: 2
            color: Qt.alpha(Commons.Color.foreground, .15)
            Rectangle {
                x: Math.min(fader.visualPosition, .5) * parent.width
                width: Math.abs(fader.visualPosition - .5) * parent.width
                height: parent.height
                radius: 2
                color: Commons.Color.accent
            }
            Rectangle {
                x: parent.width / 2 - 1
                y: -3
                width: 2
                height: parent.height + 6
                color: Qt.alpha(Commons.Color.foreground, .35)
            }
        }
        handle: Rectangle {
            x: fader.leftPadding + fader.visualPosition * (fader.availableWidth - width)
            y: fader.topPadding + (fader.availableHeight - height) / 2
            implicitWidth: 16
            implicitHeight: 16
            radius: 8
            color: fader.pressed ? Commons.Color.accent : Commons.Color.foreground
            border.width: fader.visualFocus ? 2 : 0
            border.color: Commons.Color.accent
        }
    }
    component Divider: Rectangle {
        Layout.fillWidth: true
        Layout.preferredHeight: 1
        Layout.topMargin: 4
        Layout.bottomMargin: 4
        color: Qt.alpha(Commons.Color.foreground, .12)
    }
    KeyboardPanel {
        id: popup
        bar: root.bar
        owner: root
        anchorItem: icon
        open: root.opened
        contentWidth: fittedContentWidth(Style.space(440))
        contentHeight: fittedContentHeight(panelHeader.implicitHeight + panelContent.implicitHeight + panelFooter.implicitHeight + 36, Style.space(880))
        ColumnLayout {
            anchors.fill: parent
            spacing: 16
            ColumnLayout {
                id: panelHeader
                Layout.fillWidth: true
                spacing: 12
                Copy {
                    text: "Accentum"
                    font.pixelSize: 22
                    font.bold: true
                }
                Copy {
                    text: root.hp.connected ? (root.values.battery === undefined ? "" : root.values.battery + "% battery") + (root.values.charging === 1 ? " · Charging" : root.values.charging === 2 ? " · Charged" : "") + (root.values.codec ? " · " + root.values.codec : "") : "Not connected"
                    opacity: .6
                }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    Action {
                        text: "Headphones"
                        checked: root.tab === "headphones"
                        checkable: true
                        enabled: true
                        onClicked: root.tab = "headphones"
                    }
                    Action {
                        text: "Equalizer"
                        checked: root.tab === "eq"
                        checkable: true
                        enabled: true
                        onClicked: root.tab = "eq"
                    }
                }
                Divider {}
            }
            Controls.ScrollView {
                id: settingsScroll
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: 80
                clip: true
                rightPadding: Math.max(Style.space(12), verticalScroll.implicitWidth) + Style.space(8)
                contentWidth: availableWidth
                contentHeight: panelContent.implicitHeight
                Controls.ScrollBar.horizontal.policy: Controls.ScrollBar.AlwaysOff
                Controls.ScrollBar.vertical: Controls.ScrollBar {
                    id: verticalScroll
                    parent: settingsScroll
                    x: settingsScroll.width - width
                    y: settingsScroll.topPadding
                    width: implicitWidth
                    height: settingsScroll.availableHeight
                    implicitWidth: Style.space(18)
                    orientation: Qt.Vertical
                    policy: Controls.ScrollBar.AsNeeded
                    visible: size < 1
                    interactive: true
                    hoverEnabled: true
                    padding: Style.space(4)
                    minimumSize: Math.min(1, Style.space(40) / Math.max(1, height))
                    contentItem: Rectangle {
                        radius: width / 2
                        color: Qt.alpha(Commons.Color.accent, verticalScroll.pressed ? 1 : verticalScroll.hovered ? .85 : .6)
                    }
                    background: Rectangle {
                        radius: width / 2
                        color: Qt.alpha(Commons.Color.foreground, verticalScroll.hovered ? .12 : .06)
                    }
                }
                ColumnLayout {
                    id: panelContent
                    width: settingsScroll.availableWidth
                    spacing: 20
                    Copy {
                        visible: !!root.workerError
                        text: root.workerError
                    }
                    Copy {
                        visible: !!root.snapshot.message
                        text: root.snapshot.message || ""
                        opacity: .8
                    }
                    Section {
                        visible: !root.hp.connected
                        title: "Connection"
                        Copy {
                            text: root.hp.error || (root.hp.reconnecting ? "Connecting to the headphones…" : "Settings need the headphones paired with this computer's Bluetooth. Audio can still come through a USB dongle.")
                            opacity: .8
                        }
                        Repeater {
                            model: root.hp.reconnecting ? [] : (root.snapshot.devices || [])
                            Action {
                                required property var modelData
                                visible: !!modelData.paired
                                text: "Connect " + modelData.name
                                onClicked: root.send({
                                    action: "connect",
                                    address: modelData.address
                                })
                            }
                        }
                        Action {
                            visible: !!root.hp.reconnecting
                            text: "Stop reconnecting"
                            onClicked: root.send({
                                action: "disconnect"
                            })
                        }
                    }
                    ColumnLayout {
                        visible: root.tab === "headphones" && !!root.hp.connected
                        Layout.fillWidth: true
                        spacing: 20
                        Section {
                            title: "Noise control"
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 8
                                Action {
                                    text: "Noise cancelling"
                                    checkable: true
                                    checked: root.values.transparency === 0
                                    onClicked: root.change("transparency", 0)
                                }
                                Action {
                                    text: "Transparency"
                                    checkable: true
                                    checked: root.values.transparency === 1
                                    onClicked: root.change("transparency", 1)
                                }
                            }
                            ValueSetting {
                                title: "Wind noise reduction"
                                settingKey: "anti_wind"
                                choices: [
                                    {
                                        name: "Off",
                                        value: 0
                                    },
                                    {
                                        name: "Automatic",
                                        value: 2
                                    },
                                    {
                                        name: "Maximum",
                                        value: 1
                                    }
                                ]
                            }
                        }
                        Section {
                            title: "Calls"
                            ValueSetting {
                                title: "Hear your own voice"
                                settingKey: "sidetone"
                                hint: "How much of your own voice is mixed in during calls."
                                choices: [
                                    {
                                        name: "Off",
                                        value: 0
                                    },
                                    {
                                        name: "Low",
                                        value: 1
                                    },
                                    {
                                        name: "High",
                                        value: 2
                                    }
                                ]
                            }
                        }
                        Section {
                            title: "Device"
                            Copy {
                                text: (root.values.model || "ACCENTUM") + " · Firmware " + (root.values.firmware || "unknown")
                            }
                            Action {
                                text: "Show paired devices"
                                onClicked: root.send({
                                    action: "paired_read"
                                })
                            }
                            Repeater {
                                model: root.snapshot.paired_devices || []
                                Copy {
                                    required property var modelData
                                    text: (modelData.name || "Unnamed device") + " · " + (modelData.connected ? "Connected" : "Not connected") + (modelData.own ? " · This computer" : "")
                                }
                            }
                            Action {
                                text: "Disconnect controls"
                                onClicked: root.send({
                                    action: "disconnect"
                                })
                            }
                            Hint {
                                text: "Frees this computer's control link. Audio is not affected."
                            }
                        }
                    }
                    ColumnLayout {
                        visible: root.tab === "eq" && !!root.hp.connected
                        Layout.fillWidth: true
                        spacing: 20
                        Section {
                            title: "Sound mode"
                            RowLayout {
                                Layout.fillWidth: true
                                Copy {
                                    text: "Podcast mode"
                                }
                                OnOff {
                                    id: podcastSwitch
                                    enabled: !root.busy
                                    onClicked: root.change("audio_mode", checked ? 2 : 1)
                                    Binding {
                                        target: podcastSwitch
                                        property: "checked"
                                        value: root.podcast
                                        restoreMode: Binding.RestoreNone
                                    }
                                }
                            }
                            Hint {
                                text: "Makes speech clearer. The equalizer is paused while it is on."
                            }
                        }
                        Section {
                            title: "Equalizer"
                            subtitle: root.podcast ? "Paused while Podcast mode is on" : root.eq.needs_apply ? "The headphones' equalizer was reset. Apply to restore it." : "Stored on the headphones, so it applies to every device"
                            Choice {
                                id: presetChoice
                                model: ["Custom"].concat(root.presets.map(function (preset) {
                                    return preset.name;
                                }))
                                enabled: !root.busy && !root.podcast
                                onCountChanged: Qt.callLater(function () {
                                    presetChoice.currentIndex = root.draftPreset;
                                })
                                onActivated: {
                                    if (currentIndex > 0)
                                        root.draftGains = root.presets[currentIndex - 1].gains.slice();
                                }
                                Binding {
                                    target: presetChoice
                                    property: "currentIndex"
                                    value: root.draftPreset
                                    restoreMode: Binding.RestoreNone
                                }
                            }
                            Repeater {
                                model: root.bands
                                RowLayout {
                                    id: bandRow
                                    required property int index
                                    required property string modelData
                                    Layout.fillWidth: true
                                    spacing: 10
                                    Copy {
                                        text: bandRow.modelData
                                        Layout.fillWidth: false
                                        Layout.preferredWidth: 56
                                    }
                                    Fader {
                                        id: bandSlider
                                        Layout.fillWidth: true
                                        from: -6
                                        to: 6
                                        stepSize: 0.5
                                        snapMode: Controls.Slider.SnapAlways
                                        enabled: !root.busy && !root.podcast
                                        onMoved: root.setDraftGain(bandRow.index, value)
                                        Binding {
                                            target: bandSlider
                                            property: "value"
                                            value: root.draftGains[bandRow.index] || 0
                                            when: !bandSlider.pressed
                                            restoreMode: Binding.RestoreNone
                                        }
                                    }
                                    Copy {
                                        text: root.formatGain(root.draftGains[bandRow.index] || 0)
                                        horizontalAlignment: Text.AlignRight
                                        Layout.fillWidth: false
                                        Layout.preferredWidth: 64
                                    }
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                Copy {
                                    text: "Bass boost"
                                }
                                OnOff {
                                    id: bassSwitch
                                    enabled: !root.busy && !root.podcast
                                    onClicked: root.draftBass = checked
                                    Binding {
                                        target: bassSwitch
                                        property: "checked"
                                        value: root.draftBass
                                        restoreMode: Binding.RestoreNone
                                    }
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 8
                                Action {
                                    text: root.draftDirty || root.eq.needs_apply ? "Apply to headphones" : "Applied"
                                    enabled: !root.busy && !root.podcast && (root.draftDirty || !!root.eq.needs_apply)
                                    onClicked: root.send({
                                        action: "eq_write",
                                        gains: root.draftGains,
                                        bass_boost: root.draftBass
                                    })
                                }
                                Action {
                                    text: "Revert"
                                    enabled: !root.busy && root.draftDirty
                                    onClicked: root.resetDraft()
                                }
                            }
                            Hint {
                                text: "Boosting a band lowers the overall level slightly so the sound does not distort, as in the Sennheiser app."
                            }
                        }
                    }
                }
            }
            ColumnLayout {
                id: panelFooter
                Layout.fillWidth: true
                spacing: 12
                Divider {}
                Action {
                    text: root.busy ? "Working…" : "Refresh"
                    onClicked: root.send({
                        action: "refresh"
                    })
                }
            }
        }
    }
}
