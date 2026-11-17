import QtQuick 2.0;
import calamares.slideshow 1.0;

Presentation {
    id: presentation
    Slide {
        Image { id: background; source: "slide1.png"; width: 800; height: 450; fillMode: Image.PreserveAspectCrop; anchors.centerIn: parent }
        Text {
            anchors.horizontalCenter: background.horizontalCenter; anchors.top: background.top; anchors.topMargin: 40
            text: "Installing Odeifried.\nWhen it is done: press Super+Space and tell your computer what to do."
            color: "white"; font.pixelSize: 20; horizontalAlignment: Text.AlignHCenter
        }
    }
    function onActivate() { }
    function onLeave() { }
}
